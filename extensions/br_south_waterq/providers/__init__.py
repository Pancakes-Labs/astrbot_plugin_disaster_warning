"""One module for each state. Each state agency has a different API.

A provider module has these names:

- UF: the state code, for example "RS".
- SOURCE_LABEL: the text of the source line, for example "FEPAM / PROCERGS".
- async fetch(session) -> list[dict]: get all points as common points.
- async complete(session, points) -> list[dict]: add the sample dates to the changed
  points. A provider that already gives the dates returns the points as they are.

A common point is a dict with these keys (see common.point):
key, uf, beach, municipio, ibge, latitude, longitude, laboratorio, status
("suitable", "unsuitable" or None), bulletin, sample_date (a date or None), raw.

To add a state, add a module here and add one line to states.py.
"""
