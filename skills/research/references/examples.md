# Verify a version-specific fact

```text
web_search {"query":"site:docs.python.org/3.12/library/csv.html DictReader","limit":3}
browse {"url":"https://docs.python.org/3.12/library/csv.html","max_chars":16000}
```

The URL is an example of a primary source for Python 3.12 CSV behavior. Inspect
the returned page before citing it. Record which passage supports the claim and
whether the result was truncated. A search snippet alone does not establish the
answer. If browse fails, report that failure and use another accessible original
source; do not write that the page was read.

A useful evidence row is `claim | source URL | version/date | supporting text`.
Keep inference separate from the source's statement. A fixture or old recording
cannot verify whether a release is current today.
