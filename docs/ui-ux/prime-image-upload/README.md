# Prime image upload recovery

The previous browser request had no deadline. An unresolved request or response
body left the chip in its loading state indefinitely. Submit synchronously
returned after appending an “Aspetta che l’immagine finisca di caricare” system
row. It neither waited for completion nor resumed the requested send. Failed
uploads also removed the file from the composer, forcing selection again.

The corrected composer keeps the local preview, bounds upload and JSON parsing
to ten seconds, exposes errors and an explicit retry beside the photo, and
aborts removal. Send waits for every attachment and continues once; duplicate
submit clicks cannot create another request. Failure or removal keeps the text
and cancels that intent. Retrying only saves the photo, allowing a fresh Send.

Only transient composer state changes. The durable transcript/provider turn
begins after accepted paths are available. Multipart storage, authentication,
CSRF headers, relative mount paths, voice, scene and delegation execution retain
their existing contracts.

## Evidence and limits

`tests/test_prime_image_upload.py` extracts the real upload/submit code and
styles, substitutes the provider and controls response failures. Before images
use `e9d29e76`; after images show a timeout error beside the retained photo and
draft, with the transcript empty. The fixture moves the actions into the form
as the real layout does. It contains no personal messages or real attachments.

| Viewport | Before | After |
| --- | --- | --- |
| Desktop, 1200 × 800 | [Before](before-1200.png) | [After](after-1200.png) |
| Narrow, 650 × 800 | [Before](before-650.png) | [After](after-650.png) |
| Mobile, 360 × 800 | [Before](before-360.png) | [After](after-360.png) |

The real HTTP fixture uses an ephemeral loopback port and temporary attachment
directory, calls `handle_prime_upload` with the browser's actual multipart,
then substitutes only the provider's terminal SSE. A 204,800-byte image was
saved, included in one message and completed in 38 ms in the first run. This
is fixture latency, not a measured live upload or a service guarantee.

The exact network condition behind the reported historical stall was not
captured. Six live TCP connections and a responsive health probe alone cannot
prove browser connection starvation. A complete 200 KB multipart is inexpensive
in the direct handler audit (about 1 ms); incomplete socket bodies can wait for
the handler's existing timeout. The fix bounds the browser failure and preserves
recovery without claiming a particular unobserved transport cause.

Verification uses isolated home, state, config and default-workspace paths.
`--noconftest` avoids launching the repository's application-server fixture;
the browser and focused handler tests supply their own fixtures. No production
server restart or live upload is part of this verification.

The upload, streaming, observer continuity, identity, recap re-entry, timestamps,
attachment and mobile layout checks passed: 90 tests, with three baseline image
capture cases intentionally skipped unless their capture option is enabled.
An additional response-body stall case also passes with the same deadline.
JavaScript syntax and whitespace checks passed. Initial streaming fixture
failures came from an unisolated default workspace selecting Codex for Claude
test doubles; isolating that path resolved them without product changes.
