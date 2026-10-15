# Command Bridge Prime file exchange

Use the paperclip in the Prime composer to attach raster images, PDF, TXT,
Markdown, CSV, JSON, DOCX, XLSX or PPTX. Multiple files are supported. The
configured upload limit applies (20 MiB by default, including multipart
overhead). Unsupported formats and recognizable credential filenames are
rejected. Upload errors stay beside the draft with an explicit retry.

Prime receives a real inbox path and bounded text extraction where available.
Binary office formats require a compatible reading tool; uploading a document
does not guarantee automatic conversion or preview.

For a produced file, Prime calls `prime_deliver_file` with its exact path. The
shared Codex/Claude tool accepts workspace/inbox files or a file explicitly
named in a completed delegation result from the same Prime session. It saves
an immutable snapshot and checksum, then appends a durable delivery message.
The browser displays a **Scarica** link and **Apri** for supported preview types.
Sites/apps are delivered as ZIP archives containing README/instructions;
download does not install, execute or publish them.

Downloads use an opaque ID and the current WebUI authentication/Prime identity,
never a browser-supplied filesystem path. Inline content has a sandbox CSP and
`nosniff`; generated HTML cannot execute scripts with WebUI privileges.
Credential/private paths, unsafe ZIP members and symbolic links are rejected.

Delivery cards belong to their saved message. Reload and repeated polling show
one card per artifact; cards leave the active view with their message under
the 50-message history window. Existing download URLs remain valid while the
private snapshot is retained. There is no automatic cleanup in this feature.

## State and verification

- Private attachment inbox: accepted input files and bounded extraction notes.
- Private artifact registry/snapshots: delivered bytes, identity, checksum.
- Durable Prime transcript: public file metadata and original message index.
- Model context: current-turn references; document binaries are not embedded.
- Browser scene: cards rebuilt from retained transcript messages, without
  appending old artifacts to the latest conversation.

`tests/test_prime_file_exchange_http.py` runs a bounded loopback HTTP server
with synthetic authentication and replaces only the external provider result.
The browser uses real upload, Prime Codex prompt construction, MCP file-tool
invocation, snapshot, authenticated download, store reconstruction and reload.
It covers desktop/narrow/mobile, invalid origin, anonymous download, byte
identity and history aging. No operational account or live process is used.

Operator activation is separate from integration. After activation, attach a
harmless document, ask Prime to return a file, download it on desktop/phone and
reload the chat. An end-to-end external model run is not certified by the
synthetic-provider test.
