# Esri Automation and Processing Scripts

Ready-to-run **GIS automation and data-processing products** built on Esri's platform (ArcGIS Online / Enterprise, Survey123,
Field Maps, ArcGIS API for Python) with clean object-oriented Python. Each product is **self-contained**: clone the repo (or copy one
product folder), fill in the placeholders in its `.env`, and run it.

> **You need an Esri account for production use.** See [docs/esri-account-requirements.md](docs/esri-account-requirements.md).
> Products that can run offline say so and ship a no-Esri demo mode.

## Products

| Product | Industry / scenario | Status | Offline demo |
|---|---|---|---|
| [gas-electric-leak-response](products/gas-electric-leak-response) | Dual-service utility: underground gas leak near electric equipment - webhook intake, weather, plume, electric-asset intersect, hazard grade, layer update, dual PDF reports, 17:00 reconciliation | Logic tested offline; Esri calls **not yet verified on a live org** | Yes |

More products will be added under `products/`. See [docs/adding-a-new-product.md](docs/adding-a-new-product.md) for the conventions every product follows.

## Repository layout

```
products/<product-name>/     one self-contained product (own README, .env.example, requirements, src, tests, docs)
docs/                        repo-wide guides (Esri requirements, conventions)
.github/workflows/ci.yml     tests every product + checks auto-generated README flow charts
```

## Conventions (every product)

* **Config**: secrets/URLs in `.env` (template: `.env.example`), field mappings and tunables in YAML under `config/`. Nothing hard-coded.
* **Object-oriented**: stages/clients behind small interfaces; Esri access isolated in one client class so logic is testable offline.
* **Flow charts are generated from the code** (Mermaid), never hand-drawn, so they cannot drift.
* **Honest verification**: each README states what was tested, what was not, and a checklist for what you must verify against your own org.
* **Safe defaults**: dry-run options for anything that creates or edits content in your org.

## License
No license file has been chosen yet - add one before others reuse the code.
