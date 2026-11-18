"""Sign-in and approvals for a firm's remote clients (decisions 0023, 0028).

`oauth` checks tokens from the firm's identity provider, `sign_in` reads the opt-in sign-in file
that turns those checks on in `serve_http`, and `approval_desk` and `approval_page` let a person
approve a waiting call in a browser, which a firm's own server can add through `build_http_app`.
`qaos serve` imports this package only over HTTP, and uses it only with a sign-in file.
"""
