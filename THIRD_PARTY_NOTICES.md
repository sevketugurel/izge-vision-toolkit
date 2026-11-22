# Third-party notices 

This is a source-only distribution. The dependency manifests and lockfiles are authoritative for package versions and platforms. Packages installed separately retain their upstream licenses. This inventory is not a clearance to distribute a wheel, container or model.

## Redistribution boundaries

| Component | License | Why it matters |
|---|---|---|
| psycopg, psycopg-binary 3.2.12 | LGPL-3.0-only | Allowed as an unmodified dependency. Ship the LGPL text and a notice with any image or bundle; do not statically combine or modify without complying. |
| PyAV 19.0.0 (wheel bundles FFmpeg) | PyAV: BSD-3-Clause. Bundled FFmpeg: **GPL-enabled build** (libx264 and libx265 are present in `av.codecs_available`) | Distributing the wheel or a Docker image that contains it conveys GPL-licensed binaries: GPL text, source offer, and notice are required. Distributing only this repository's source (users install PyAV themselves) does not convey them. Options for a public image: build PyAV against an LGPL-only FFmpeg, or do not publish images. |
| certifi 2026.11.20, tqdm 4.70.1 | MPL-2.0 (tqdm: MPL-2.0 AND MIT) | File-level copyleft; unmodified use is fine. Keep notices. |
| lightningcss (frontend build tool) | MPL-2.0 | Build tool, not shipped in the bundle. |
| DINOv3 weights (not in repo) | Meta DINOv3 custom license | Weights are downloaded at runtime with a user token; attribution "Built with DINOv3" required where the model is used. |
| SAM 3.x, RF-DETR weights (not in repo) | SAM: Meta custom license; RF-DETR Seg-L: Apache-2.0 | Per-model terms; see docs/THIRD_PARTY_LICENSES.md. |
| Ultralytics YOLO | AGPL-3.0 | Not distributed, declared or imported. |

## Python packages (locked)

| Package | Version | License | Class |
|---|---|---|---|
| alembic | 1.17.0 | MIT | permissive |
| annotated-doc | 0.0.5 | MIT | permissive |
| annotated-types | 0.8.0 | MIT | permissive |
| anyio | 4.15.1 | MIT | permissive |
| av | 19.0.0 | BSD-3-Clause | permissive |
| cachetools | 7.2.4 | MIT | permissive |
| certifi | 2026.11.20 | Mozilla Public License 2.0 (MPL 2.0) | WEAK-COPYLEFT |
| cffi | 2.1.1 | MIT-0 | permissive |
| charset-normalizer | 3.5.2 | MIT | permissive |
| click | 8.5.0 | BSD-3-Clause | permissive |
| cloudpickle | 3.1.2 | BSD License | permissive |
| colorama | 0.4.6 | BSD License | permissive |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | permissive |
| databricks-sdk | 0.156.0 | Apache Software License | permissive |
| fastapi | 0.143.0 | MIT | permissive |
| filelock | 4.3.0 | MIT | permissive |
| fsspec | 2026.9.0 | BSD-3-Clause | permissive |
| gitdb | 4.0.12 | BSD License | permissive |
| gitpython | 3.2.1 | BSD-3-Clause | permissive |
| google-api-core | 2.42.0 | Apache Software License | permissive |
| google-auth | 2.61.0 | Apache Software License | permissive |
| google-cloud-core | 2.8.0 | Apache Software License | permissive |
| google-cloud-storage | 3.17.0 | Apache Software License | permissive |
| google-crc32c | 1.9.0 | Apache-2.0 | permissive |
| google-resumable-media | 2.11.0 | Apache Software License | permissive |
| googleapis-common-protos | 1.75.5 | Apache-2.0 | permissive |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | permissive |
| h11 | 0.16.0 | MIT License | permissive |
| hf-xet | 1.7.0 | Apache-2.0 | permissive |
| httpcore | 1.0.9 | BSD-3-Clause | permissive |
| httpcore2 | 2.13.1 | BSD-3-Clause | permissive |
| httpx | 0.28.1 | BSD License | permissive |
| httpx2 | 2.13.1 | BSD-3-Clause | permissive |
| httpx2-jsfetch | 1.0 | BSD-3-Clause | permissive |
| huggingface-hub | 2.2.0 | Apache Software License | permissive |
| idna | 3.20 | BSD-3-Clause | permissive |
| importlib-metadata | 9.1.0 | Apache-2.0 | permissive |
| iniconfig | 2.3.1 | MIT | permissive |
| jinja2 | 3.1.6 | BSD License | permissive |
| joblib | 1.6.0 | BSD-3-Clause | permissive |
| mako | 1.4.3 | MIT | permissive |
| markdown-it-py | 4.2.0 | MIT License | permissive |
| markupsafe | 3.0.4 | BSD-3-Clause | permissive |
| mdurl | 0.1.2 | MIT License | permissive |
| mlflow-skinny | 3.17.2 | Apache Software License | permissive |
| mpmath | 1.3.0 | BSD License | permissive |
| narwhals | 2.27.1 | MIT | permissive |
| networkx | 3.7 | BSD-3-Clause | permissive |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | permissive |
| opentelemetry-api | 1.45.2 | Apache-2.0 | permissive |
| opentelemetry-proto | 1.45.1 | Apache-2.0 | permissive |
| opentelemetry-sdk | 1.45.2 | Apache-2.0 | permissive |
| opentelemetry-semantic-conventions | 0.66b2 | Apache-2.0 | permissive |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | permissive |
| pillow | 13.0.0 | MIT-CMU | permissive |
| pluggy | 1.6.0 | MIT License | permissive |
| proto-plus | 1.29.0 | Apache Software License | permissive |
| protobuf | 7.36.2 | 3-Clause BSD License | permissive |
| psutil | 7.2.2 | BSD-3-Clause | permissive |
| psycopg | 3.2.12 | LGPL-3.0-only | WEAK-COPYLEFT |
| psycopg-binary | 3.2.12 | LGPL-3.0-only | WEAK-COPYLEFT |
| pyarrow | 26.0.0 | Apache-2.0 | permissive |
| pyasn1 | 0.6.5 | BSD-2-Clause | permissive |
| pyasn1-modules | 0.4.2 | BSD License | permissive |
| pycparser | 3.11 | BSD-3-Clause | permissive |
| pydantic | 2.13.5 | MIT | permissive |
| pydantic-core | 2.46.5 | MIT | permissive |
| pygments | 2.21.0 | BSD-2-Clause | permissive |
| pytest | 9.0.3 | MIT | permissive |
| python-dotenv | 1.2.4 | BSD-3-Clause | permissive |
| python-multipart | 0.0.32 | Apache-2.0 | permissive |
| pyyaml | 6.0.3 | MIT License | permissive |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | permissive |
| requests | 2.34.2 | Apache Software License | permissive |
| rich | 15.0.0 | MIT License | permissive |
| ruff | 0.14.0 | MIT License | permissive |
| safetensors | 0.8.0 | Apache Software License | permissive |
| scikit-learn | 1.9.1 | BSD-3-Clause | permissive |
| scipy | 1.18.1 | BSD License | permissive |
| setuptools | 84.2.1 | MIT | permissive |
| shellingham | 1.5.4 | ISC License (ISCL) | permissive |
| smmap | 5.0.3 | BSD License | permissive |
| sqlalchemy | 2.0.44 | MIT | permissive |
| sqlparse | 0.6.0 | BSD License | permissive |
| starlette | 1.7.0 | BSD-3-Clause | permissive |
| sympy | 1.14.0 | BSD License | permissive |
| threadpoolctl | 3.7.0 | BSD-3-Clause | permissive |
| tokenizers | 0.23.3 | Apache Software License | permissive |
| torch | 2.14.1 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | permissive |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | WEAK-COPYLEFT |
| transformers | 5.19.0 | Apache 2.0 License | permissive |
| truststore | 0.10.4 | MIT | permissive |
| typer | 0.27.3 | MIT | permissive |
| typing-extensions | 4.16.0 | PSF-2.0 | permissive |
| typing-inspection | 0.4.4 | MIT | permissive |
| tzdata | 2026.5 | Apache-2.0 | permissive |
| urllib3 | 2.8.0 | MIT | permissive |
| uvicorn | 0.52.4 | BSD-3-Clause | permissive |
| zipp | 4.1.2 | MIT | permissive |

## Selected npm dependencies (frontend)

| Package | Version | License | Dev only |
|---|---|---|---|
| @oxc-project/types | 0.153.0 | MIT | yes |
| @playwright/test | 1.64.0 | Apache-2.0 | yes |
| @rolldown/binding-darwin-arm64 | 1.2.13 | MIT | yes |
| @rolldown/pluginutils | 1.0.1 | MIT | yes |
| @types/react | 19.2.17 | MIT | yes |
| @types/react-dom | 19.2.3 | MIT | yes |
| csstype | 3.2.3 | MIT | yes |
| detect-libc | 2.1.2 | Apache-2.0 | yes |
| fdir | 6.5.0 | MIT | yes |
| framer-motion | 14.6.0 | MIT | no |
| fsevents | 2.3.3 | MIT | yes |
| lightningcss | 1.33.0 | MPL-2.0 | yes |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 | yes |
| motion | 14.6.0 | MIT | no |
| motion-dom | 14.6.0 | MIT | no |
| motion-utils | 14.6.0 | MIT | no |
| nanoid | 3.3.20 | MIT | yes |
| picocolors | 1.1.1 | ISC | yes |
| picomatch | 4.0.7 | MIT | yes |
| playwright | 1.64.0 | Apache-2.0 | yes |
| playwright-core | 1.64.0 | Apache-2.0 | yes |
| postcss | 8.5.29 | MIT | yes |
| react | 19.2.7 | MIT | no |
| react-dom | 19.2.7 | MIT | no |
| rolldown | 1.2.13 | MIT | yes |
| scheduler | 0.27.0 | MIT | no |
| source-map-js | 1.2.2 | BSD-3-Clause | yes |
| tinyglobby | 0.2.17 | MIT | yes |
| tslib | 2.8.1 | 0BSD | no |
| typescript | 6.0.3 | Apache-2.0 | yes |
| vite | 8.3.8 | MIT | yes |

Upstream licensing and model attribution:

- PyAV: [BSD-3-Clause](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt); [FFmpeg licensing](https://ffmpeg.org/legal.html) depends on the binary build. No FFmpeg or PyAV binaries or Docker images are distributed here.
- [Psycopg licensing](https://www.psycopg.org/psycopg3/docs/) is separate from this code license. No driver wheel or `libpq` is bundled.
- [RF-DETR upstream](https://github.com/roboflow/rf-detr): the selected Seg Small and Seg Large checkpoints are identified by the checked-in model manifests. The project does not ship checkpoints, and no license for a particular Seg checkpoint is assumed to cover other weights or Plus variants.
- [Built with DINOv3](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md): optional Meta model under its own license, with access and attribution conditions. Model weights are not included.
- See [frontend/licenses/](frontend/licenses/) for preserved upstream frontend license texts and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for its own attribution. All upstream notices remain in force.
