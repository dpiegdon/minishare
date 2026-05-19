# minishare API

A file server: browse, download, upload, make directories, delete.
Add `?format=json` to any listing for a JSON response; mutating
endpoints return JSON to non-browser clients and redirect browsers.
This same text is served at `GET $BASE/help`.

`$BASE` is this server's base URL. `$path` and `$dir` are placeholders;
`$path` is relative to the share root and "../" or absolute paths are
rejected (400).

## Authentication

If the server requires HTTP Basic auth it answers `401` until
credentials are sent. Put them in a curl config file `ms.curl`
containing one line:

```
user = "USER:PASS"
```

and pass it on each request with `-K`:

```
curl -sS -K ms.curl '$BASE/browse/?format=json'
```

`ms.curl` holds the password in cleartext.

## Endpoints

```
Browse (HTML):      GET    $BASE/
Browse (JSON):      GET    $BASE/browse/$path?format=json
Download a file:    GET    $BASE/get/$path
View inline:        GET    $BASE/get/$path?inline=1
Upload (multipart): POST   $BASE/upload[/$dir]    field name: file
Upload (raw body):  PUT    $BASE/put/$path        body = file contents
Make a directory:   POST   $BASE/mkdir/$path      (mkdir -p)
Delete file/dir:    DELETE $BASE/delete/$path
                    (bulk: POST $BASE/delete with repeated sel=$path)
This help (text):   GET    $BASE/help
```

Destructive operations require an explicit flag, else `409`:

```
Overwrite a file:   ?overwrite=1   PUT or multipart upload onto an
                                   existing file (otherwise 409).
Delete a tree:      ?recursive=1   a NON-EMPTY directory (otherwise
                                   409). A file or empty directory
                                   needs no flag.
```

A `409` means nothing was deleted or overwritten; the response body
names the target and the flag that re-authorizes it. With the flag the
operation proceeds and the change is irreversible.

## curl examples

```bash
# if auth is enabled, add  -K ms.curl  to each command (see Authentication)

# list the root as JSON
curl -sS '$BASE/browse/?format=json'

# download a file
curl -sS -O '$BASE/get/notes/todo.txt'

# upload via multipart form into the 'docs' folder
curl -sS -F file=@report.pdf '$BASE/upload/docs'

# upload raw bytes to an exact path (parent dirs auto-created;
# add ?overwrite=1 to replace an existing file, else 409)
curl -sS -T report.pdf '$BASE/put/docs/report.pdf'

# create a directory (parents included)
curl -sS -X POST '$BASE/mkdir/docs/2026'

# delete a file or an empty directory
curl -sS -X DELETE '$BASE/delete/docs/note.txt'

# delete a non-empty directory and everything in it
curl -sS -X DELETE '$BASE/delete/docs/old-stuff?recursive=1'
```

## Notes

* PUT creates missing parent directories; replacing an existing file
  needs ?overwrite=1 (multipart upload too), else 409.
* mkdir is idempotent. Deleting a non-empty directory needs
  ?recursive=1, else 409 (nothing is deleted; bulk delete is
  all-or-nothing). A file or empty directory needs no flag.
* With no auth configured, anyone who can reach the server can delete.
* Uploads may return 413 if a per-upload or total-storage limit is set;
  downloads and deletes are unaffected. The HTML pages show storage use.
