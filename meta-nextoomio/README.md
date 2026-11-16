# meta-nextoomio

OpenEmbedded / Yocto layer for the **nextoomio** audio operating system (`AD-01`, `SYS-03–SYS-07`).

## Layer Dependencies

- `openembedded-core` (`meta`)
- `meta-openembedded/meta-oe`
- `meta-openembedded/meta-multimedia`
- `meta-openembedded/meta-networking`
- `meta-raspberrypi`
- `meta-swupdate`

## Reference Target (`v0.0.1`)

- Machine: `raspberrypi4-64`
- Reference audio profile: `hifiberry-amp` ("HiFiBerry Amp", for Raspberry Pi 4 + HiFiBerry Amp+, see `files/hardware-profiles/hifiberry-amp.json`)
- Partition layout: `files/wic/nextoomio-ab.wks.in` (MBR dual A/B slots + persistent `/data`)

## License

Layer metadata is licensed under the MIT License (`COPYING.MIT`). Original nextoomio components build Apache-2.0 sources from `../core` (`nx-sysd` in Phase 0; core and audio services in v0.0.1) and `../webui` (v0.0.1).
