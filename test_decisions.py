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

    def test_new_close_goal_overrides_stale_route(self):
        self.bot.goal = [-7, 0]
        self.bot.last_goal = self.obs["t"]
        self.obs["cubes"] = [dict(id=5000, pos=[7, 0, 0])]
        self.assertGreater(self.bot.act(self.obs)["move"][0], .9)

    def test_close_goal_behind_wall_uses_waypoint(self):
        self.obs["cubes"] = [dict(id=5000, pos=[7, 0, 0])]
        self.bot.arena.solid = lambda x, y, z, pad=0: 2 <= x < 3 and abs(y) <= 2
        self.bot.arena.waypoint = lambda *args: (0, 8)
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


if __name__ == "__main__":
    KIT = sys.argv.pop(1)
    unittest.main(verbosity=2)
