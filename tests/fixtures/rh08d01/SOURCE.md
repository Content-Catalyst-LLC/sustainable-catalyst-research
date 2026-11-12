# RH-08D-01 discovery fixtures (synthetic)

Every record here is **invented**. The bodies follow the public response shapes of the OpenAlex works API and the
Crossref REST API `works` route, but no record, title, author, venue or DOI comes from either provider.

* **DOIs:** they use the `10.5555` test prefix, and none is a real registration.
* **ORCID:** `0000-0002-1825-0097` is the fictional example identifier used in ORCID's own documentation.
* **Landing pages:** they point to `example.org`.

The cases the tests rely on:
- DOI variants: resolver URL, `doi:` prefix, trailing punctuation, upper case;
- an in-page duplicate and a cross-provider duplicate;
- year and venue conflicts;
- a DOI-less record that matches a DOI-bearing one;
- a preprint and article pair with different DOIs;
- a retracted work and a retraction notice;
- records without an id, title or valid DOI;
- a provider validation failure, an unexpected body and a non-JSON body.
