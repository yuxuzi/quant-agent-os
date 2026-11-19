# Larchmere on Azure: deployment descriptors (not run in the tests)

| File | What it is |
| --- | --- |
| `Dockerfile` | the fund's image on the platform's image, built in Azure Container Registry |
| `containerapp.bicep` | Container Apps environment and app, Log Analytics, Key Vault references, Azure Files, PostgreSQL Flexible Server |
| `larchmere.env` | the settings the app receives; secrets come from Key Vault through `secretRef` |
| `sign-in.toml` | Entra ID sign-in, through the fund's introspection shim |

The estate they serve is modelled locally (see `../../facility/warehouse/build_larchmere.py`):
ADLS Gen2 containers `raw` and `curated`, an Azure SQL Database, and a SharePoint library exported to Blob.
