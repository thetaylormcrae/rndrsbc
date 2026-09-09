"""
rndrSBC - Flask-based web dashboard (replaces the stdlib http.server).

Architecture modeled on InkyPi (GPL-3.0): blueprint-per-domain, Flask signed
session cookies, CSRF-protected mutations, and a real config model validated
on every write via core.config_schema.
"""
