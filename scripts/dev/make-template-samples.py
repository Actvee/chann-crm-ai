"""Write the starter template .docx files to disk, to look at one locally.

The app serves these from `GET /api/v1/document-template-samples/{type}`,
generated per request — they are NOT committed as binaries, for the
reason spelled out in `services/documents/samples.py`: a .docx in Git
cannot be reviewed in a diff and drifts silently from the placeholder
vocabulary.

This script exists so a person can still open one in Word:

    python3 scripts/dev/make-template-samples.py [outdir]

It prints, for each sample, the placeholders it contains and whether any
of them resolve to nothing against the real snapshot — which is the same
assertion `tests/unit/test_template_samples.py` makes, so a mismatch here
means a failing test, not a surprise.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "application"))

from chann_app.services.documents.docx import convert_docx_to_html  # noqa: E402
from chann_app.services.documents.fill import (  # noqa: E402
    placeholders_in, unknown_placeholders,
)
from chann_app.services.documents.samples import (  # noqa: E402
    SAMPLE_DOCUMENT_TYPES, build_sample_docx, sample_docx_filename, sample_snapshot,
)

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
out.mkdir(parents=True, exist_ok=True)

problems = 0
for document_type in SAMPLE_DOCUMENT_TYPES:
    content = build_sample_docx(document_type)
    path = out / sample_docx_filename(document_type)
    path.write_bytes(content)
    html = convert_docx_to_html(content, filename=path.name)
    snapshot = sample_snapshot(document_type)
    blank = unknown_placeholders(html, snapshot)
    print(f"{path}  ({len(content):,} bytes)")
    print(f"  {len(placeholders_in(html))} placeholders, "
          f"{len(html):,} chars of HTML after conversion")
    if blank:
        problems += 1
        print(f"  RESOLVES TO NOTHING: {blank}")

print("every placeholder in every sample resolves" if not problems
      else f"{problems} sample(s) contain a placeholder that resolves to nothing")
sys.exit(1 if problems else 0)
