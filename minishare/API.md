# minishare API

The HTML pages are just a UI. This API is self-service for agents and
scripts: add `?format=json` to any listing for a JSON response, and
mutating endpoints already return JSON to non-browser clients. This same
text is served live (with `$BASE` filled in) at `GET $BASE/help`.

`$BASE` is this server's base URL (shown live at the top of every page).
`$path` and `$dir` are placeholders you replace; `$path` is relative to
the share root and "../" or absolute paths are rejected.

## Authentication

This server may require HTTP Basic auth - the `401` response and
`GET $BASE/help` say so. Do NOT pass credentials on the command line:
`curl -u USER:PASS` (and creating the file below with `echo`/`printf`)
leaks the password into your shell history and the system process list
(anyone can see it via `ps`). Keep the secret in a file curl reads.

Preferred - a curl config file. Create `ms.curl` with your editor or
file tool (not a shell redirect), containing exactly:

```
user = "USER:PASS"
```

Lock it down and use `-K` on every request - nothing secret ever
reaches argv or the history file:

```
chmod 600 ms.curl
curl -sS -K ms.curl '$BASE/browse/?format=json'
```

Alternative - a `.netrc` file `ms.netrc` (HOST = the host part of
`$BASE`, no scheme or port) containing exactly:

```
machine HOST login USER password PASS
```

then `curl -sS --netrc-file ms.netrc '$BASE/browse/?format=json'`.
Either file is plaintext at rest: keep tight permissions and delete it
when done - this only keeps the secret out of history and `ps`, not
off disk.

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

Destructive ops fail closed - you must opt in, in the request:
  Overwrite a file:   add ?overwrite=1   (PUT or multipart upload;
                       without it, replacing an existing file is 409)
  Delete a tree:      add ?recursive=1   (a NON-EMPTY directory;
                       without it, deleting it is 409. A plain file or
                       an empty directory needs no flag.)
A 409 names exactly what would be lost and is the only thing standing
between a stray request and irreversible data loss - treat it as a
"are you sure?" you must answer deliberately, not retry blindly.
```

## curl examples

```bash
# auth enabled? add  -K ms.curl  to every command below (see
# Authentication) - never put -u USER:PASS on the command line

# list the root as JSON
curl -sS '$BASE/browse/?format=json'

# download a file
curl -sS -O '$BASE/get/notes/todo.txt'

# upload via multipart form into the 'docs' folder
curl -sS -F file=@report.pdf '$BASE/upload/docs'

# upload raw bytes to an exact path (parent dirs auto-created;
# add ?overwrite=1 only if you intend to replace an existing file)
curl -sS -T report.pdf '$BASE/put/docs/report.pdf'

# create a directory (parents included)
curl -sS -X POST '$BASE/mkdir/docs/2026'

# delete a file or an empty directory (no flag needed)
curl -sS -X DELETE '$BASE/delete/docs/note.txt'

# delete a NON-EMPTY directory and everything in it (explicit opt-in)
curl -sS -X DELETE '$BASE/delete/docs/old-stuff?recursive=1'
```

## Notes

* PUT creates missing parent directories; replacing an existing file
  needs ?overwrite=1 (multipart upload too), else 409.
* mkdir is idempotent. Deleting a non-empty directory needs
  ?recursive=1, else 409 (and nothing is deleted - bulk delete is
  all-or-nothing). A file or empty directory needs no flag.
* No auth configured == anyone who can reach the server can also delete.
* Uploads may return 413 if a per-upload or total-storage limit is set;
  downloads and deletes always work. The HTML pages show storage use.
