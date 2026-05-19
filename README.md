# bulk-reducto-converter-v2

Bulk multi-type document → Markdown converter. POST a batch of files, get back a zip of `.md` files. See [SPEC.md](SPEC.md) for the full design.

## Local

```sh
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Docker

```sh
docker build -t bulk-conv .
docker run --rm -p 8000:8000 -e OCR=docling bulk-conv
```

## Configuration

| Var                 | Default                          | Purpose                                              |
| ------------------- | -------------------------------- | ---------------------------------------------------- |
| `OCR`               | `docling`                        | `docling` (local, no API key) or `reducto` (hosted)  |
| `REDUCTO_API_KEY`   | —                                | required only when `OCR=reducto` or as OCR fallback  |
| `REDUCTO_API_URL`   | `https://platform.reducto.ai`    | Reducto base URL                                     |
| `MAX_UPLOAD_BYTES`  | `209715200` (200 MiB)            | cumulative batch cap                                 |
| `MAX_FILES_PER_JOB` | `50`                             | per-request file count cap                           |
| `PORT`              | `8000`                           | bind port                                            |

## Supported types

`.md`, `.markdown`, `.txt`, `.csv`, `.docx`, `.xlsx`, `.xlsm`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.tif`. Anything else returns a per-file error in `errors.txt` inside the output zip.
