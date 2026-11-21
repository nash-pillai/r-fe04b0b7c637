"""Focused decisions on open ground using the official recording's rules and messages."""
# SPDX-License-Identifier: MIT
import copy
import gzip
import json
import math
import sys
import unittest

from bot import Bot, angle


KIT = None


class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if KIT is None:
            raise unittest.SkipTest("Run test_decisions.py /path/to/mech-kit")
        with gzip.open(f"{KIT}/check/stream.jsonl.gz", "rt") as stream:
            cls.hello = json.loads(next(stream))
            cls.first_obs = json.loads(next(stream))
        cls.hello["map"]["dir"] = f"{KIT}/arena"
        cls.template = Bot(cls.hello)

    def setUp(self):
        self.bot = copy.copy(self.template)
        self.bot.config = copy.deepcopy(self.template.config)
        self.bot.arena = copy.copy(self.template.arena)
        self.bot.arena.height = lambda x, y: 0
        self.bot.arena.solid = lambda *args: False
        self.obs = copy.deepcopy(self.first_obs)
        for field in ("mechs", "cubes", "pickups", "crates", "events", "projectiles", "drops"):
            self.obs[field] = []
        self.me = self.obs["self"]
        self.me.update(pos=[0, 0, 0], aim=[0, 0], vel=[0, 0, 0])
        self.obs["zone"].update(center=[0, 0], next_center=[0, 0], radius=200,
                                next_radius=100, shrink_start=60, shrink_end=110)

    def enemy(self, x, y, vy=0):
        enemy = dict(id=7, kind="mech", pos=[x, y, 0], vel=[0, vy, 0], hp=1400, cubes=0)
        self.obs["mechs"].append(enemy)
        return enemy

    def equip(self, kind):
        weapon = dict(kind=kind, stats=self.bot.config["weapons"][kind], cooldown=0)
        self.me["weapons"][1] = weapon
        return weapon

    def test_cube_just_outside_live_radius(self):
        for radius in (3.5, 2):
            with self.subTest(radius=radius):
                self.bot.config["cubes"]["pickup_radius"] = radius
                self.obs["cubes"] = [dict(id=5000, pos=[radius + .25, 0, 0])]
                self.assertGreater(self.bot.act(self.obs)["move"][0], .9)

    def test_inside_cube_radius(self):
        self.obs["cubes"] = [dict(id=5000, pos=[2, 0, 0])]
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_stationary_bot_dodges_incoming_projectile(self):
        self.me.update(grounded=True, in_zone=True)
        self.obs["projectiles"] = [
            dict(id=1, kind="gun", pos=[100, 0, 4], vel=[-250, 0, 0]),
        ]
        move = self.bot.act(self.obs)["move"]
        self.assertGreater(abs(move[1]), .8)

    def test_stationary_bot_ignores_departing_projectile(self):
        self.me.update(grounded=True, in_zone=True)
        self.obs["projectiles"] = [
            dict(id=1, kind="gun", pos=[1, 0, 4], vel=[250, 0, 0]),
        ]
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_stationary_bot_stays_inside_tiny_zone(self):
        self.me.update(grounded=True, in_zone=True)
        self.obs["zone"].update(radius=2, next_radius=2)
        self.obs["projectiles"] = [
            dict(id=1, kind="gun", pos=[100, 0, 4], vel=[-250, 0, 0]),
        ]
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_new_close_goal_overrides_stale_route(self):
        self.bot.goal = [-7, 0]
        self.bot.last_goal = self.obs["t"]
        self.obs["cubes"] = [dict(id=5000, pos=[7, 0, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][0], .9)

    def test_close_goal_behind_wall_uses_waypoint(self):
        self.obs["cubes"] = [dict(id=5000, pos=[7, 0, 0])]
        self.bot.arena.solid = lambda x, y, z, pad=0: 2 <= x < 3 and abs(y) <= 2
        self.bot.arena.waypoint = lambda *args, **kwargs: (0, 8)
        self.assertGreater(self.bot.act(self.obs)["move"][1], .9)

    def test_contested_cube_keeps_escape_direction(self):
        self.enemy(15, 0)
        self.obs["cubes"] = [dict(id=5000, pos=[7, 0, 0])]
        self.assertLess(self.bot.act(self.obs)["move"][0], 0)

    def test_tiny_zone_approach_from_rest(self):
        self.me["in_zone"] = False
        self.obs["zone"].update(center=[.45, 0], next_center=[.45, 0],
                                radius=.2, next_radius=0, shrink_start=0)
        action = self.bot.act(self.obs)
        move = action["move"]
        self.assertGreater(move[0], 0)
        match = self.bot.config["match"]
        interval = match["decide_every"] / match["tick_hz"]
        speed = self.me["stats"]["boost_speed" if action["boost"] else "walk_speed"]
        self.assertLessEqual(math.hypot(*move) * speed * interval, .45 + 1e-8)

    def test_shrinking_zone_allows_loot_inside_next_ring(self):
        self.obs["t"] = 80
        self.me["in_zone"] = True
        self.obs["zone"].update(center=[0, 0], next_center=[30, 0], radius=160)
        self.obs["cubes"] = [dict(id=5000, pos=[-6, 0, 0])]
        self.assertLess(self.bot.act(self.obs)["move"][0], -.9)

    def test_shrinking_zone_prioritizes_intercept_near_edge(self):
        self.obs["t"] = 80
        self.me.update(pos=[-115, 0, 0], in_zone=True)
        self.obs["zone"].update(center=[0, 0], next_center=[20, 0],
                                radius=120, next_radius=60)
        self.obs["cubes"] = [dict(id=5000, pos=[-118, 0, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][0], .9)

    def test_shrinking_zone_skips_loot_outside_projected_ring(self):
        self.obs["t"] = 103
        self.me["in_zone"] = True
        self.obs["zone"].update(center=[0, 0], next_center=[20, 0],
                                radius=100, next_radius=70)
        self.obs["cubes"] = [dict(id=5000, pos=[-84, 0, 0])]
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def final_ring_route(self, loot_xy, center_xy, start_time):
        arena = self.bot.arena = copy.copy(self.template.arena)
        # Use the exact grid coordinates so a cached terminal waypoint is at our feet.
        loot = arena.xy(arena.cell(loot_xy))
        center = arena.xy(arena.cell(center_xy))
        self.obs["zone"].update(center=center, next_center=center, next_radius=0,
                                shrink_start=390, shrink_end=415, dps=260)
        self.obs["cubes"] = [dict(id=5000, pos=[*loot, arena.height(*loot)])]
        self.me["grounded"] = True
        self.ring_observation(center, start_time)
        self.assertEqual(arena.goal, arena.cell(loot))
        self.obs["cubes"] = []
        return loot, center

    def ring_observation(self, xy, now):
        zone = self.obs["zone"]
        zone["radius"] = min(25, zone["shrink_end"] - now)
        self.me.update(pos=[*xy, self.bot.arena.height(*xy)],
                       in_zone=math.dist(xy, zone["center"]) <= zone["radius"])
        self.obs.update(t=now, tick=round(now * self.bot.config["match"]["tick_hz"]))
        return self.bot.act(self.obs)

    def test_urgent_ring_leaves_collected_cube_on_campus(self):
        loot, center = self.final_ring_route((-137.38, 46.91), (-147.38, 46.91), 394.4)
        for now in (395.8, 399., 405.1):
            with self.subTest(now=now):
                action = self.ring_observation(loot, now)
                self.assertLess(action["move"][0], -.99)
                self.assertEqual(action["boost"], not self.me["in_zone"])
                self.assertEqual(self.bot.arena.goal, self.bot.arena.cell(center))

    def test_urgent_ring_replans_around_campus_obstacle(self):
        loot, center = self.final_ring_route((-122.38, 56.91), (-112.38, 46.91), 387.)
        arena = self.bot.arena
        action = self.ring_observation(loot, 397.)
        distance = math.dist(loot, center)
        direct = [(b-a)/distance for a,b in zip(loot, center)]
        stats = self.me["stats"]
        body = (stats["radius"] + .3, stats["height"], math.tan(math.radians(stats["max_slope_deg"])))
        self.assertFalse(arena.traversable(self.me["pos"], *direct, distance, *body))
        self.assertLess(action["move"][1], -.99)
        self.assertTrue(arena.traversable(self.me["pos"], *action["move"], 8, *body))
        path = arena.path
        self.ring_observation(loot, 397.1)
        self.assertIs(arena.path, path)

    def test_approach_half_meter_does_not_probe_beyond_goal(self):
        self.bot.arena.solid = lambda x, y, z, pad=0: x >= 1.5
        self.assertTrue(self.bot.arena.traversable((0, 0, 0), 1, 0, .5, 2.8, 9))

    def test_live_uphill_limit(self):
        self.me["stats"]["max_slope_deg"] = 30
        self.bot.arena.height = lambda x, y: x * math.tan(math.radians(40))
        self.obs["cubes"] = [dict(id=5000, pos=[9, 0, 7.55])]
        move = self.bot.act(self.obs)["move"]
        self.assertLessEqual(move[0] * math.tan(math.radians(40)),
                             math.tan(math.radians(30)) + 1e-8)

    def test_recovery_faces_known_threat(self):
        self.enemy(50, 0)
        self.me["hp"] = .3 * self.me["max_hp"]
        action = self.bot.act(self.obs)
        half_arc = math.radians(self.me["stats"]["front_arc_deg"]) / 2
        self.assertLess(abs(angle(action["aim"][0])), half_arc)
        self.assertEqual(action["fire"], [False, False])

    def test_gun_cooling_until_after_slew(self):
        target = self.enemy(100, 0, vy=22)
        gun = self.me["weapons"][0]
        gun["cooldown"] = .08
        laser = self.equip("laser")
        self.me["aim"] = list(self.bot.aim_at(self.me, target, gun)[:2])
        action = self.bot.act(self.obs)
        self.assertEqual(action["aim"], list(self.bot.aim_at(self.me, target, laser)[:2]))
        self.assertEqual(action["fire"], [False, True])

    def test_aligned_gun_and_laser(self):
        target = self.enemy(100, 0)
        self.equip("laser")
        self.me["aim"] = list(self.bot.aim_at(self.me, target, self.me["weapons"][0])[:2])
        self.assertEqual(self.bot.act(self.obs)["fire"], [True, True])

    def test_cooling_gun_and_mortar_use_one_pitch(self):
        target = self.enemy(120, 0)
        gun = self.me["weapons"][0]
        gun["cooldown"] = .08
        mortar = self.equip("mortar")
        self.me["aim"] = list(self.bot.aim_at(self.me, target, gun)[:2])
        action = self.bot.act(self.obs)
        self.assertEqual(action["aim"], list(self.bot.aim_at(self.me, target, mortar)[:2]))
        self.assertFalse(action["fire"][0])

    def test_low_energy_preserves_reserve(self):
        self.enemy(20, 0)
        self.equip("lightning")
        self.me["energy"] = self.bot.config["weapons"]["lightning"]["energy"] + 10
        action = self.bot.act(self.obs)
        self.assertFalse(action["boost"])
        self.assertFalse(action["fire"][1])

    def opponent(self, ident, x, y, hp=1400, weapon="gun", aiming_at_us=True):
        # The new fixtures assert tactical headings on open ground.
        self.bot.arena.waypoint = lambda pos, goal, exact_cell=False: goal
        enemy = self.enemy(x, y)
        yaw = math.atan2(-y, -x) if aiming_at_us else math.atan2(y, x)
        enemy.update(id=ident, hp=hp, max_hp=1400, weapons=[weapon], aim=[yaw, 0])
        return enemy

    def test_clear_finisher_over_nearer_covered_mech(self):
        self.opponent(2, 30, 0)
        target = self.opponent(4, 54, 36, hp=220)
        self.bot.arena.solid = lambda x, y, z, pad=0: 12 < x < 18 and abs(y) < 3
        action = self.bot.act(self.obs)
        yaw = math.atan2(target["pos"][1], target["pos"][0])
        self.assertAlmostEqual(action["aim"][0], yaw)
        self.assertGreater(sum(a*b for a,b in zip(action["move"], [math.cos(yaw), math.sin(yaw)])), .25)

    def test_focus_survives_small_range_change_and_switches_for_finish(self):
        self.opponent(2, 86, 20, aiming_at_us=False)
        other = self.opponent(4, 90, -20, aiming_at_us=False)
        self.assertGreater(self.bot.act(self.obs)["aim"][0], 0)
        self.obs["t"] += .1
        other["pos"][0] = 82
        self.obs["mechs"].reverse()
        self.assertGreater(self.bot.act(self.obs)["aim"][0], 0)
        self.obs["t"] += .1
        other["hp"] = 180
        self.assertLess(self.bot.act(self.obs)["aim"][0], 0)

    def test_healthy_duel_kites_then_closes_on_wounded_opponent(self):
        enemy = self.opponent(4, 84, 0)
        self.assertLess(self.bot.act(self.obs)["move"][0], -.25)
        enemy["hp"] = 240
        self.obs["t"] += .1
        self.assertGreater(self.bot.act(self.obs)["move"][0], .25)

    def test_spacing_uses_equipped_lightning_range(self):
        self.opponent(4, 50, 0)
        lightning = self.equip("lightning")
        self.assertGreater(self.bot.act(self.obs)["move"][0], .2)
        lightning["stats"]["range"] = 80
        self.obs["t"] += .1
        self.assertLess(self.bot.act(self.obs)["move"][0], -.2)

    def test_crossfire_overrides_isolated_finish(self):
        self.opponent(2, 75, 20, hp=220)
        self.opponent(4, 80, -20)
        action = self.bot.act(self.obs)
        self.assertLess(action["move"][0], -.9)
        self.assertTrue(action["boost"])

    def test_opposite_shooters_escape_laterally(self):
        self.opponent(2, 44, 0, hp=220)
        self.opponent(4, -44, 0)
        self.assertGreater(abs(self.bot.act(self.obs)["move"][1]), .9)

    def test_loot_over_distant_distracted_or_out_of_range_shooter(self):
        enemy = self.opponent(2, 125, 0, aiming_at_us=False)
        self.obs["cubes"] = [dict(id=5000, pos=[0, 7, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][1], .9)
        enemy.update(weapons=["lightning"], aim=[math.pi, 0], hp=100)
        self.obs["t"] += .1
        self.assertGreater(self.bot.act(self.obs)["move"][1], .9)

    def test_pickup_over_covered_near_shooter(self):
        self.opponent(2, 34, 0)
        self.bot.arena.solid = lambda x, y, z, pad=0: 12 < x < 18 and abs(y) < 3
        self.obs["pickups"] = [dict(id=8001, kind="laser", pos=[0, 7, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][1], .9)

    def test_last_open_position_expires_even_with_server_tracking(self):
        enemy = self.opponent(4, 84, 0, hp=240)
        self.assertGreater(self.bot.act(self.obs)["move"][0], .25)
        self.obs["t"] += .5
        enemy["pos"] = [60, 40, 0]
        self.bot.arena.clear = lambda *args: False
        action = self.bot.act(self.obs)
        self.assertGreater(action["move"][0], .9)
        self.assertFalse(any(action["fire"]))
        self.obs["t"] += .51
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_lost_target_yields_to_cube_and_kill_cancels_pursuit(self):
        enemy = self.opponent(4, 84, 0, hp=240)
        self.bot.act(self.obs)
        self.obs["mechs"] = []
        self.obs["t"] += .1
        self.obs["cubes"] = [dict(id=5000, pos=[0, 7, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][1], .9)
        self.obs["cubes"] = []
        self.obs["mechs"] = [enemy]
        self.obs["t"] += .1
        self.bot.act(self.obs)
        self.obs["mechs"] = []
        self.obs["events"] = [dict(type="kill", target=4)]
        self.obs["t"] += .1
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_projected_edge_overrides_crossfire_escape(self):
        self.obs["t"] = 80
        self.me.update(pos=[-115, 0, 0], in_zone=True)
        self.obs["zone"].update(center=[0, 0], next_center=[20, 0], radius=120, next_radius=60)
        self.opponent(2, -65, 10)
        self.opponent(4, -65, -10)
        action = self.bot.act(self.obs)
        self.assertGreater(action["move"][0], .9)
        self.assertFalse(action["boost"])

    def test_blocked_lead_rejects_finish_but_respects_incoming_shot(self):
        enemy = self.opponent(2, 90, 0, hp=170)
        enemy["vel"] = [0, 19, 0]
        self.bot.arena.solid = lambda x, y, z, pad=0: 50 < x < 60 and y > 2
        action = self.bot.act(self.obs)
        self.assertLess(action["move"][0], -.25)
        self.assertAlmostEqual(action["aim"][0], 0)
        self.assertFalse(any(action["fire"]))

    def test_clear_lead_does_not_refresh_a_target_behind_cover(self):
        enemy = self.opponent(2, 84, 0, hp=170)
        enemy["vel"] = [0, 18, 0]
        self.bot.arena.solid = lambda x, y, z, pad=0: 65 < x < 78 and abs(y) < 3
        self.assertTrue(self.bot.aim_at(self.me, enemy, self.me["weapons"][0])[3])
        self.assertEqual(self.bot.act(self.obs)["move"], [0, 0])

    def test_recent_hit_counts_distracted_second_shooter(self):
        self.opponent(2, 75, 20, hp=220)
        self.opponent(4, 80, -20, aiming_at_us=False)
        self.obs["events"] = [dict(type="damaged", by=4, weapon="gun", amount=45, bearing=-.24)]
        action = self.bot.act(self.obs)
        self.assertLess(action["move"][0], -.9)
        self.assertTrue(action["boost"])

    def corner(self, hurt=True):
        self.opponent(2, 75, 3)
        self.opponent(4, 80, -4)
        self.me.update(grounded=True, in_zone=True)
        if hurt:
            self.me['hp'] = .3 * self.me['max_hp']
        self.bot.arena.solid = lambda x, y, z, pad=0: 20 < x < 32 and y > 3

    def open_lanes(self, xy, torso=.5):
        arena = self.bot.arena
        body = (*xy, arena.height(*xy) + torso * self.me['stats']['height'])
        rules = self.bot.config
        count = 0
        for e in self.obs['mechs']:
            x, y, z = e['pos']
            scale = 1 + rules['cubes']['size_per_cube'] * e['cubes']
            muzzle = (x, y, z + rules['mech']['muzzle_height'] * scale)
            count += arena.clear(muzzle, body)
        return count

    def test_cover_crossfire_cuts_two_lanes(self):
        self.corner(hurt=False)
        self.assertEqual(self.open_lanes((0, 0)), 2)
        move = self.bot.act(self.obs)['move']
        point = tuple(1.2 * self.me['stats']['walk_speed'] * u for u in move)
        self.assertEqual(self.open_lanes(point), 0)
        self.assertEqual(self.open_lanes(point, .9), 0)

    def campus_corner(self):
        a = self.opponent(2, -73, 41)
        b = self.opponent(4, -79, 47)
        self.bot.arena = copy.copy(self.template.arena)
        arena = self.bot.arena
        self.me['pos'] = [-119, 47, arena.height(-119, 47)]
        self.obs['zone'].update(center=[-119, 47], next_center=[-119, 47])
        for enemy in (a, b):
            x, y = enemy['pos'][:2]
            enemy['pos'][2] = arena.height(x, y)
            enemy['aim'] = [math.atan2(47-y, -119-x), 0]

    def test_cover_on_official_campus_geometry(self):
        self.campus_corner()
        self.assertEqual(self.open_lanes((-119, 47)), 2)
        move = self.bot.act(self.obs)['move']
        span = 1.2 * self.me['stats']['walk_speed']
        point = (-119 + span*move[0], 47 + span*move[1])
        self.assertTrue(self.bot.arena.traversable(self.me['pos'], *move, span, 2.8, 9))
        self.assertEqual(self.open_lanes(point), 0)
        self.assertEqual(self.open_lanes(point, .9), 0)

    def test_cover_recovering_duelist_stays_screened_from_one_shooter(self):
        self.corner()
        self.obs['mechs'].pop()
        self.bot.act(self.obs)
        self.me['pos'] = [0, 13, 0]
        self.obs['t'] += .9
        self.obs['cubes'] = [dict(id=5000, pos=[0, -2, 0])]
        self.assertEqual(self.open_lanes((0, 13)), 0)
        self.assertEqual(self.bot.act(self.obs)['move'], [0, 0])

    def arrive_in_cover(self):
        self.corner()
        self.bot.act(self.obs)
        # A second controlled observation, with both muzzles behind the corner.
        self.me['pos'] = [0, 13, 0]
        self.obs['t'] += .9
        self.obs['cubes'] = [dict(id=5000, pos=[0, -2, 0])]
        self.assertEqual(self.open_lanes(self.me['pos'][:2]), 0)
        return self.bot.act(self.obs)

    def test_cover_holds_past_repair_delay_even_after_tracking_expires(self):
        action = self.arrive_in_cover()
        self.assertEqual(action['move'], [0, 0])
        self.obs['mechs'] = []
        self.obs['t'] += self.me['stats']['regen_delay'] + .2
        self.me['hp'] += .2 * self.me['stats']['regen_per_s']
        self.assertEqual(self.bot.act(self.obs)['move'], [0, 0])
        self.obs['t'] += 4.
        self.assertLess(self.bot.act(self.obs)['move'][1], -.9)

    def test_cover_uses_live_repair_delay_and_releases_on_health(self):
        self.me['stats']['regen_delay'] = 8
        self.arrive_in_cover()
        self.obs['t'] += 9.5
        self.assertEqual(self.bot.act(self.obs)['move'], [0, 0])
        self.me['hp'] = .9 * self.me['max_hp']
        self.obs['t'] += .1
        self.assertLess(self.bot.act(self.obs)['move'][1], -.9)

    def test_cover_still_dodges_at_rest(self):
        self.arrive_in_cover()
        self.obs['t'] += .1
        self.obs['projectiles'] = [dict(id=99, kind='gun', pos=[0, 113, 4], vel=[0, -250, 0])]
        self.assertGreater(abs(self.bot.act(self.obs)['move'][0]), .8)

    def test_cover_breaks_for_new_open_lane(self):
        self.arrive_in_cover()
        self.obs['t'] += .1
        self.opponent(9, -35, 13)
        self.assertGreater(math.hypot(*self.bot.act(self.obs)['move']), .9)

    def test_cover_breaks_for_predicted_ring(self):
        self.arrive_in_cover()
        self.obs['t'] = 105
        self.obs['zone'].update(center=[0, 0], next_center=[0, -40], radius=75, next_radius=15)
        self.assertLess(self.bot.act(self.obs)['move'][1], -.9)

    def test_cover_rejects_blocked_access(self):
        self.corner(hurt=False)
        slab = self.bot.arena.solid
        self.bot.arena.solid = lambda x,y,z,pad=0: slab(x,y,z,pad) or (1 < y <= 4 and z < 3)
        # A low curb blocks travel north while both shooters can see over it.
        self.assertLess(self.bot.act(self.obs)['move'][0], -.9)

    def test_cover_rejects_hidden_point_outside_projected_ring(self):
        self.corner(hurt=False)
        self.obs['t'] = 100
        self.obs['zone'].update(center=[0,-20], next_center=[0,-20], radius=45, next_radius=26)
        move = self.bot.act(self.obs)['move']
        self.assertLessEqual(move[1], .01)

    def test_cover_low_wall_leaves_upper_body_exposed(self):
        self.corner(hurt=False)
        self.bot.arena.solid = lambda x,y,z,pad=0: 20 < x < 32 and y > 3 and z < 6.5
        self.assertLess(self.bot.act(self.obs)['move'][0], -.9)


if __name__ == "__main__":
    KIT = sys.argv.pop(1)
    unittest.main(verbosity=2)
