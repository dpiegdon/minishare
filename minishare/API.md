# minishare API

A file server: browse, download, upload, rename or move, make
directories, delete.
Add `?format=json` to any listing for a JSON response; mutating
endpoints return JSON to non-browser clients and redirect browsers.
This same text is served at `GET $BASE/help`.

`$BASE` is this server's base URL. `$path` and `$dir` are placeholders;
`$path` is relative to the share root; "../" or absolute paths are
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
Rename or move:     POST   $BASE/rename/$path     to=$newpath
Delete file/dir:    DELETE $BASE/delete/$path
                    (bulk: POST $BASE/delete with repeated sel=$path)
This help (text):   GET    $BASE/help
```

Destructive operations require an explicit flag, else `409`:

```
Overwrite a file:   ?overwrite=1   PUT, multipart upload, or rename
                                   onto an existing file (otherwise
                                   409).
Delete a tree:      ?recursive=1   a NON-EMPTY directory (otherwise
                                   409). A file or empty directory
                                   needs no flag.
```

A `409` means nothing was deleted or overwritten; the response body
names the target and the flag that re-authorizes it. With the flag the
operation proceeds and the change is irreversible.

## curl examples

```bash
# if auth is enabled, add `-K ms.curl` to each command (see Authentication)

# list the root as JSON
curl -sS '$BASE/browse/?format=json'

# download a file
curl -sS -O '$BASE/get/notes/todo.txt'

# upload via multipart form into the 'docs' folder
curl -sS -F file=@report.pdf '$BASE/upload/docs'

# upload raw bytes to an exact path (parent dirs auto-created)
curl -sS -T report.pdf '$BASE/put/docs/report.pdf'

# create a directory (parents included)
curl -sS -X POST '$BASE/mkdir/docs/2026'

# rename in place, or move: 'to' is a path relative to the share root
curl -sS -X POST '$BASE/rename/report.pdf' -d 'to=final.pdf'
curl -sS -X POST '$BASE/rename/final.pdf' -d 'to=docs/2026/final.pdf'

# delete a file or an empty directory
curl -sS -X DELETE '$BASE/delete/docs/note.txt'

# delete a non-empty directory and everything in it
curl -sS -X DELETE '$BASE/delete/docs/old-stuff?recursive=1'
```

## Notes

* Parent directories: PUT creates missing ones and mkdir is
  idempotent, but rename does not create them - a missing destination
  parent is a 404.
* rename takes to=$newpath, a path relative to the share root: a value
  with no "/" renames in place, one naming another directory moves the
  entry there. A directory is never replaced, ?overwrite=1 or not.
* Bulk delete is all-or-nothing: if one target is refused, nothing is
  deleted.
* With no auth configured, anyone who can reach the server can delete.
* Uploads may return 413 if a per-upload or total-storage limit is set;
  downloads and deletes are unaffected. The HTML pages show storage use.
