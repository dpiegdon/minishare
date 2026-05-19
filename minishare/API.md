# minishare API

The HTML pages are just a UI. This API is self-service for agents and
scripts: add `?format=json` to any listing for a JSON response, and
mutating endpoints already return JSON to non-browser clients. This same
text is served live (with `$BASE` filled in) at `GET $BASE/help`.

`$BASE` is this server's base URL (shown live at the top of every page).
`$path` and `$dir` are placeholders you replace; `$path` is relative to
the share root and "../" or absolute paths are rejected.

## Authentication

This server may require HTTP Basic auth. When it does, the `401` response
and `GET $BASE/help` say so; then send credentials with every request:

```
curl -sS -u USER:PASS '$BASE/browse/?format=json'
```

## Endpoints

```
Browse (HTML):      GET    $BASE/
Browse (JSON):      GET    $BASE/browse/$path?format=json
Download a file:    GET    $BASE/get/$path
View inline:        GET    $BASE/get/$path?inline=1
Upload (multipart): POST   $BASE/upload[/$dir]    field name: file
Upload (raw body):  PUT    $BASE/put/$path        body = file contents
Make a directory:   POST   $BASE/mkdir/$path      (mkdir -p)
Delete file or dir: DELETE $BASE/delete/$path     (dirs: RECURSIVE)
                    (bulk: POST $BASE/delete with repeated sel=$path)
This help (text):   GET    $BASE/help
```

## curl examples

```bash
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

# delete a file, or a whole directory tree
curl -sS -X DELETE '$BASE/delete/docs/old-stuff'
```

## Notes

* PUT creates missing parent directories and overwrites existing files.
* mkdir is idempotent; deleting a directory removes it RECURSIVELY.
* No auth configured == anyone who can reach the server can also delete.
* Uploads may return 413 if a per-upload or total-storage limit is set;
  downloads and deletes always work. The HTML pages show storage use.
