"""Sign-in and approvals for a firm's remote clients (decision 0023).

`oauth` checks tokens from the firm's identity provider, and `approval_desk` and `approval_page`
let a person approve a waiting call in a browser. No command serves them yet: a firm's own server
can, through `build_http_app`. The default `qaos serve` never imports this package.
"""
