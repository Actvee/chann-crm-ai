# Fonts shipped with the application

`Sarabun-Regular.ttf` and `Sarabun-Bold.ttf` — Sarabun by Cadson Demak, from
Google Fonts (https://fonts.google.com/specimen/Sarabun), licensed under the
SIL Open Font License 1.1 (https://openfontlicense.org), which permits
bundling and redistribution with this software.

The same two files live in `scripts/dev/guide-fonts/` for
`scripts/dev/render-guide-images.py`, which runs from a clone and never in
the container. These copies are the runtime ones: `services/charts.py` draws
the chat's chart pictures with them, and a container has no system Thai font
— without these every Thai label renders as tofu boxes.

`tests/unit/test_charts.py` fails when the two copies drift apart.
