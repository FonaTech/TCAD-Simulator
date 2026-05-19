# Third-Party Notices

This repository's own source code is licensed under MIT. Third-party packages remain under their own licenses. Review those licenses before redistributing binaries, hosting a service, or bundling dependencies.

## Runtime Dependencies

| Dependency | Used for | Typical license note |
| --- | --- | --- |
| NumPy | array processing and voxel fields | BSD-style |
| Matplotlib | desktop plots, image loading, fallback geometry helpers | Matplotlib license, BSD-compatible |
| SciPy | optional distance transforms and filters | BSD-style |
| scikit-image | optional marching cubes acceleration | BSD-style |
| Numba | optional JIT acceleration | BSD-style |
| cryptography | optional encrypted WebUI library/admin config | Apache-2.0/BSD dual license |
| PyQt5 | desktop GUI | GPL/commercial dual license |
| Pillow | optional image/mask helpers | HPND-style open-source license |
| imageio / imageio-ffmpeg | optional image/video export support | BSD-style; bundled FFmpeg may have additional LGPL/GPL component considerations |
| pdfminer.six | optional PDF text extraction | MIT |
| PyPDF2 | optional PDF text extraction fallback | BSD-style |
| PyMuPDF / MuPDF | optional PDF text extraction | AGPL/commercial dual license |
| gdstk | optional GDSII support | Boost Software License |
| gdspy | optional GDSII fallback | BSD-style |
| Three.js | WebUI 3D preview assets downloaded at runtime | MIT |

## License Guidance

- MIT is a good fit for this project's own source if you want broad academic and commercial reuse with minimal restrictions.
- PyQt5 is the main dependency to think about. If users install PyQt5 themselves to run this open-source app, your MIT source release can still be MIT. If you distribute a packaged binary that bundles PyQt5, GPL/commercial obligations may apply.
- PyMuPDF is optional and is intentionally not installed by default in `requirements.txt`. If installed, AGPL/commercial terms may affect redistribution or network-service use.
- Three.js files are downloaded into the runtime static directory on first WebUI launch. If you vendor those files into the repository later, keep their MIT license notice with them.
- This file is not legal advice. For commercial redistribution, get a license review.
