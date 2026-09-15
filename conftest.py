# pytest must not collect the plugin's own `__init__.py` (it is a Hermes
# provider module, importable only with the Hermes runtime on sys.path).
collect_ignore = ["__init__.py"]
