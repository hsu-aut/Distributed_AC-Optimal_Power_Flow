# Provenance

This repository is a curated release. Development happened in a separate working repository;
the commit identifiers below refer to that history and are the ones recorded in the run
manifests of the raw data.

## Software states per measurement series

| Series | Date | Software state | Contents |
|---|---|---|---|
| Preliminary cluster campaign | 15.10.2026 | `c83e90c` (frozen local state) | 77 runs, convergence checked every iteration |
| Main campaign, cooled | 19.10.2026 | `b3cb9b7` | 77 runs, convergence checked every 10 iterations, ranks spread across banks |
| Control series, uncooled | 19.10.2026 | `b3cb9b7` | same configuration and rank placement, fans off |
| PGLib cross-check | 19.10.2026 | `9ad72e2` | 16 distributed and 3 central runs with finite branch limits |
| Asynchronous pre-test | 19.10.2026 | branch `async-admm` | exploratory, not part of the campaign |

Every run directory in the raw data carries a `manifest.json` with the commit identifier, the
full argument list, the settings and the checksums of the source files. Timing figures must
only be compared within one series, because software state and thermal conditions differ
between them.

## Case data

| File | SHA-256 | Source |
|---|---|---|
| `data/matpower/case118.m` | `bc2e6f22b4b9e776572885ee4b50e4f4ab2ee0c5577e9126e86d906f14c4b5f7` | MATPOWER 8.1 |
| `data/pglib/pglib_opf_case118_ieee.m` | `b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78` | PGLib-OPF v23.07 (23 July 2023) |

Both files are unmodified, including their original provenance comments and licence notices.

**Known labelling error:** `topology_ieee118_pglib.yaml` carries the field
`source: MATPOWER 8.1 data/case118.m`, because the importer writes a fixed source string. The
recorded `source_sha256` is correct and identifies the PGLib file. The checksum is
authoritative; the source string is not. The file is left unchanged here, because it is an
input of the recorded runs.

## Raw data

The compact evidence per campaign is contained in `validation/`: manifests, results, AC
validation reports, rank metrics and thermal records. The complete raw data, including
per-iteration residual traces, agent logs and checkpoints, is about 1.4 GB and is deposited
separately.
