"""Document intelligence.

  extract.py    structured extraction per format (PDF, DOCX, XLSX, CSV, TXT/MD/code, PPTX, images) with page/
                slide/sheet locations and real tables; OCR for images and scanned PDF pages (ocr.py)
  chunking.py   location-aware chunks for indexing
  access.py     who may do what with which document (owner, or read-only viewers it is shared with)
  store.py      PostgreSQL persistence (files, chunks, tables, access grants)
  retrieval.py  hybrid retrieval (pgvector + full-text, rank fusion) restricted to accessible documents
  tables.py     table profiling and sandboxed pandas analysis
  generate.py   document generation (DOCX, XLSX, PPTX, PDF, Markdown) from a structured spec
  service.py    ingestion, summaries, RAG Q&A, comparison, data extraction, analysis, generation
  tools.py      the chat agent's document tools
"""
