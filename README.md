# Arena controller

The bot reads `mechfight/1` observations and produces movement, aiming, weapon, and upgrade decisions. It uses the supplied map for collision checks, incremental grid navigation, and line-of-fire queries.

The current policy kites opponents, seeks weapons and cubes, leads projectile targets, releases charged lasers after alignment, and moves toward the next safe zone. Practice games contain a cooperating opponent squad. Final games are free-for-alls, so performance in practice does not establish final strength.

## Build and check

```sh
docker build -t arena-bot .
python3 /path/to/mechfight-kit/check/check.py --map-dir /arena -- \
  docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v /path/to/mechfight-kit/arena:/arena:ro arena-bot
```

The official check replays recorded observations. It verifies protocol behavior and response deadlines, but does not simulate actions or measure placement.

`navigation.json` is a compressed walkability grid derived from the supplied arena. Its world checksum must match before it is used. If the map changes, the bot rebuilds the grid during initialization. Terrain and building files are mounted by the scorer and are not bundled in this repository.

## Sources and licensing

`mechfight.py` is the unmodified Python protocol wrapper from the organizers' `mechfight-kit.tar.gz`. The navigation cache derives from the kit's map. The bot was implemented during the event with AI assistance.

The project is licensed under MIT. Upstream starter material remains subject to the organizer's applicable license.
