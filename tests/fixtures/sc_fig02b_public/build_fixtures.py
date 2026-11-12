#!/usr/bin/env python3
"""SC-FIG-02B public-release fixtures (SYNTHETIC, no scientific meaning). Re-running is byte-identical.

Two synthetic figures bound to the synthetic article ``synthetic-soil-carbon-scenarios``, laid out as
the public release store (``store/<bundle sha256>/<file sha256>``):

    SYN-PUB-soil-carbon   modelled scenario lines with a 90% band (Vega-Lite view, canonical-JSON hash)
    SYN-PUB-plot-means    hypothetical plot means with reported ± (Plotly view, canonical-JSON hash)

Hashes follow the shared canonical JSON rule: json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False) as UTF-8. FIXTURES.json lists each bundle hash for tests and staging seeding.
"""
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "store"
ARTICLE = "synthetic-soil-carbon-scenarios"
VL5 = "https://vega.github.io/schema/vega-lite/v5.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def pretty(value):
    return (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def bundle(figure_id, title, evidence, status, sources, calcs, context, rendering, artifact):
    return {
        "schema": "sc.figure-bundle.v1", "figureId": figure_id, "version": 1, "title": title,
        "project": {"projectRef": "research-entry:" + ARTICLE, "ownerScope": "isolated-synthetic", "environment": "staging"},
        "publication": {"kind": "technical-supplement", "target": "research-entry:" + ARTICLE, "bindingStatus": "synthetic-test"},
        "evidenceClass": evidence, "figureType": "evidence-chart", "sourceRefs": sources, "calculationRefs": calcs,
        "scientificContext": {**context, "measurementStatus": status},
        "rendering": {"profileId": "sc-institutional-research-v1", "palette": "colorblind-safe", "accessibilityReview": "passed",
                      "visualizationSpecSha256": artifact["sha256"], **rendering},
        "artifacts": [artifact],
        "provenance": {"authorityOwner": "workspace", "sourceSnapshotIds": [s["sourceId"] for s in sources],
                       "generationRunId": "synthetic-run:" + artifact["sha256"][:24], "generatedAt": "2026-11-11T00:00:00Z",
                       "reproducibility": "synthetic-fixture-tested"},
        "review": {"scientific": "approved", "visual": "approved", "rights": "approved", "sourceIntegrity": "approved",
                   "publication": "approved", "reviewerId": "synthetic-reviewer", "reviewedAt": "2026-11-11T00:00:00Z",
                   "notes": "Synthetic staging review record; no real review took place."},
    }


def soil_carbon():
    rows = []
    for year in range(0, 21, 2):
        for scenario, rate in (("cover crops (synthetic)", 0.42), ("reduced tillage (synthetic)", 0.25)):
            mid = round(rate * year * (1 - year / 60), 3)
            rows.append({"year": year, "soc": mid, "lo": round(mid * 0.75, 3), "hi": round(mid * 1.25, 3),
                         "scenario": scenario, "evidenceClass": "modelled"})
    data_sha = sha(rows)
    y_title = "Soil organic carbon change (t C ha-1)"
    view = {
        "$schema": VL5, "width": 620, "height": 340,
        "title": "SYNTHETIC — soil organic carbon scenarios (not real field data)",
        "data": {"values": rows},
        "layer": [
            {"mark": {"type": "errorband", "opacity": 0.25},
             "encoding": {"x": {"field": "year", "type": "quantitative"}, "y": {"field": "lo", "type": "quantitative", "title": y_title},
                          "y2": {"field": "hi"}, "color": {"field": "scenario", "type": "nominal"}}},
            {"mark": {"type": "line", "point": True},
             "encoding": {"x": {"field": "year", "type": "quantitative"}, "y": {"field": "soc", "type": "quantitative"},
                          "color": {"field": "scenario", "type": "nominal"},
                          "strokeDash": {"field": "evidenceClass", "type": "nominal", "legend": {"title": "Evidence class"}}}},
        ],
        "usermeta": {"sc": {
            "profile": "sc-vega-lite-v1", "datasetId": "synthetic:soil-carbon-scenarios-v1", "datasetSha256": data_sha,
            "evidenceField": "evidenceClass",
            "fields": {"year": {"label": "Years since practice change", "unit": "years"},
                       "soc": {"label": "Soil organic carbon change", "unit": "t C ha-1"},
                       "lo": {"label": "Lower bound", "unit": "t C ha-1"}, "hi": {"label": "Upper bound", "unit": "t C ha-1"},
                       "scenario": {"label": "Scenario", "unit": "category"}, "evidenceClass": {"label": "Evidence class", "unit": "category"}},
            "uncertainty": {"kind": "range", "lowerField": "lo", "upperField": "hi",
                            "statement": "Synthetic ±25% scenario band; not a statistical interval."}}},
    }
    vh = sha(view)
    artifact = {"format": "json", "artifactRef": f"sc://workspace/figure/SYN-PUB-soil-carbon/{vh}.json", "sha256": vh,
                "mediaType": "application/vnd.vegalite.v5+json", "width": 620, "height": 340, "status": "staging-verified", "revision": 1}
    params = {"model": "synthetic saturating accumulation", "rates": [0.42, 0.25], "horizon": 60, "fixture": True}
    b = bundle(
        "SYN-PUB-soil-carbon", "SYNTHETIC soil organic carbon scenarios with a scenario band", "modelled", "computed-from-specified-inputs",
        [{"sourceId": "synthetic:soil-carbon-parameters-v1", "provider": "Sustainable Catalyst synthetic fixture",
          "identityStatus": "source-hash-verified", "qualityStatus": "not-applicable", "licenceStatus": "verified-for-use", "sha256": sha(params)},
         {"sourceId": "synthetic:placeholder-citation", "provider": "Placeholder standing in for a published study",
          "citationId": "SYNTHETIC-CITATION-1", "doi": "10.0000/synthetic.0001",
          "identityStatus": "source-identified", "qualityStatus": "pending", "licenceStatus": "pending"}],
        [{"system": "other", "runRef": "synthetic-run:soil-carbon-scenarios-v1", "method": "synthetic saturating accumulation, two practice scenarios",
          "outputSha256": data_sha, "assumptionRefs": ["synthetic:soil-carbon-parameters-v1"]}],
        {"interpretation": "Synthetic modelled scenario lines used to exercise public Research figures.",
         "limitations": ["Synthetic values with no scientific meaning", "Not field measurements; no carbon sequestration is measured or certified"],
         "units": "t C ha-1", "uncertaintyNotation": "Synthetic ±25% scenario band (lower and upper bound fields).",
         "claimIds": [], "doNotInfer": ["any scientific conclusion", "measured soil carbon change"]},
        {"renderer": "vega-lite", "rendererVersion": "vega-lite 6.5.0; vega 6.4.0",
         "caption": "SYNTHETIC: modelled soil organic carbon change under two practice scenarios, with a ±25% band.",
         "altText": "Two rising synthetic scenario lines over 20 years with shaded bands; cover crops rise faster than reduced tillage."},
        artifact)
    return b, view


def plot_means():
    plots = ["Plot A (synthetic)", "Plot B (synthetic)", "Plot C (synthetic)", "Plot D (synthetic)"]
    means = [1.8, 2.4, None, 3.1]
    errors = [0.4, 0.5, None, 0.6]
    figure = {
        "data": [{"type": "scatter", "mode": "markers", "x": plots, "y": means, "name": "Hypothetical plot mean",
                  "error_y": {"type": "data", "array": errors, "visible": True, "color": "#0072B2", "thickness": 1.5, "width": 6},
                  "marker": {"color": "#0072B2", "size": 9, "symbol": "circle"},
                  "hovertemplate": "%{x}: %{y} t C ha-1<extra></extra>"}],
        "layout": {"title": {"text": "SYNTHETIC — hypothetical plot means with reported ± (Plot C not reported)"},
                   "xaxis": {"title": {"text": "Plot"}}, "yaxis": {"title": {"text": "Soil organic carbon stock change (t C ha-1)"}},
                   "width": 620, "height": 340, "showlegend": False, "font": {"family": "system-ui, sans-serif"}},
        "config": {"displaylogo": False, "responsive": True},
    }
    ph = sha(figure)
    artifact = {"format": "json", "artifactRef": f"sc://workspace/figure/SYN-PUB-plot-means/{ph}.json", "sha256": ph,
                "mediaType": "application/vnd.plotly.v1+json", "width": 620, "height": 340, "status": "staging-verified", "revision": 1}
    table = {"plots": plots, "means": means, "errors": errors}
    b = bundle(
        "SYN-PUB-plot-means", "SYNTHETIC hypothetical plot means with reported uncertainty", "hypothetical", "hypothetical-only",
        [{"sourceId": "synthetic:plot-means-table-v1", "provider": "Sustainable Catalyst synthetic fixture",
          "identityStatus": "source-hash-verified", "qualityStatus": "not-applicable", "licenceStatus": "verified-for-use", "sha256": sha(table)}],
        [],
        {"interpretation": "Hypothetical plot means used to exercise the Plotly path of public Research figures.",
         "limitations": ["Synthetic values for placeholder plots", "Plot C has no reported value and is not interpolated"],
         "units": "t C ha-1", "uncertaintyNotation": "Reported ± per plot (synthetic).",
         "claimIds": [], "doNotInfer": ["any scientific conclusion", "a value for Plot C"]},
        {"renderer": "plotly", "rendererVersion": "plotly.js 3.3.1",
         "caption": "SYNTHETIC: hypothetical plot means with reported ±; Plot C is missing.",
         "altText": "Four synthetic plots; three show means between 1.8 and 3.1 with error bars, Plot C has no value."},
        artifact)
    return b, figure


def main():
    if STORE.exists():
        shutil.rmtree(STORE)
    index = {"description": "SYNTHETIC SC-FIG-02B public-release fixtures; no scientific meaning.",
             "article": ARTICLE, "figures": []}
    for b, view in (soil_carbon(), plot_means()):
        raw = pretty(b)
        digest = hashlib.sha256(raw).hexdigest()
        folder = STORE / digest
        folder.mkdir(parents=True)
        (folder / digest).write_bytes(raw)
        (folder / b["artifacts"][0]["sha256"]).write_bytes(pretty(view))
        index["figures"].append({"figureId": b["figureId"], "bundleSha256": digest, "evidenceClass": b["evidenceClass"],
                                 "title": b["title"], "viewSha256": b["artifacts"][0]["sha256"],
                                 "mediaType": b["artifacts"][0]["mediaType"]})
    (HERE / "FIXTURES.json").write_bytes(pretty(index))


if __name__ == "__main__":
    main()
