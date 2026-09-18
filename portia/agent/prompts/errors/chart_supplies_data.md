`{key}` is portia's to set, and it is at `{path}` in the spec you sent.

The rows come from the `SELECT` you wrote — portia runs it in the sandbox, puts the
result in the spec as `data`, and sends it to the browser. A spec carrying its own data
is a chart that can disagree with its own query, and there would be no way to tell from
looking at it which half was true. `url` would also reach a network the sandbox does
not have.

Portia sets `$schema`, `data` and `datasets`. Everything else in the spec is yours.

Call it again without `{key}`.
