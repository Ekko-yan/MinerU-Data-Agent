# MinerU local document parsing setup

This folder contains a local Python 3.11 conda environment and a wrapper script
for parsing unstructured documents with MinerU.

## Environment

The environment was created at:

```powershell
D:\vscodepro\MinerU\.venv_mineru
```

Activate it:

```powershell
conda activate D:\vscodepro\MinerU\.venv_mineru
```

Installed package:

```powershell
python -m pip show mineru
```

Current installed MinerU version: `3.1.15`.

## Optional model pre-download

MinerU can download models during the first parse. To pre-download pipeline
models from ModelScope:

```powershell
mineru-models-download -s modelscope -m pipeline
```

For all local VLM/hybrid models, use:

```powershell
mineru-models-download -s modelscope -m all
```

## Parse documents

Put PDFs, images, DOCX, PPTX, or XLSX files in a folder such as `input_docs`,
then run:

```powershell
python .\process_documents_with_mineru.py -i .\input_docs -o .\outputs -b pipeline -m auto -l ch
```

For a single file:

```powershell
python .\process_documents_with_mineru.py -i .\input_docs\demo.pdf -o .\outputs
```

The wrapper writes MinerU's native files plus:

- `outputs\structured_manifest.json`: document-level index of generated files.
- `outputs\structured_chunks.jsonl`: normalized text/table/image/equation chunks.

## Backend notes

- `pipeline` is the default in the wrapper because it is the most practical CPU
  path for Windows.
- `hybrid-auto-engine` and `vlm-auto-engine` can be more accurate, but usually
  need substantially more local model/GPU capacity.
- `vlm-http-client` and `hybrid-http-client` require `--server-url` pointing to
  an OpenAI-compatible MinerU model server.

You can inspect MinerU CLI options with:

```powershell
mineru --help
```
