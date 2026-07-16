"""NSW source adapters for Greater Sydney (see docs/AUSTRALIA.md).

Each module mirrors its Victorian counterpart's interface exactly, so
build.py can stay adapter-agnostic. Sources (confirmed by the Sydney Probe
recon runs):
  crime      BOCSAR quarterly suburb dataset + LGA workbook (blob store)
  prices     NSW Valuer General PSI bulk sales
  rents      Fair Trading rental bond lodgements (postcode workbooks)
  zoning     EPI Land Zoning via the ePlanning ArcGIS MapServer
  transport  TfNSW station entries/exits + station locations
  schools    NSW DoE master dataset (government schools)
"""
