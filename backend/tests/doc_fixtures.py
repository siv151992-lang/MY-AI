"""Builders for test documents in every supported format (generated in memory, no binary fixtures)."""
import io
def png_with_text(text, size=(1000, 220)):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", size, "white"); d = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    d.text((30, 70), text, fill="black", font=font)
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()
def scanned_pdf(text):
    from PIL import Image
    img = Image.open(io.BytesIO(png_with_text(text, (1240, 400)))); b = io.BytesIO(); img.save(b, "PDF", resolution=150); return b.getvalue()
def docx_bytes():
    import docx
    d = docx.Document(); d.add_heading("Payment terms", 1); d.add_paragraph("Invoices are due within 30 days.")
    t = d.add_table(rows=3, cols=2); t.cell(0,0).text="Item"; t.cell(0,1).text="Price"; t.cell(1,0).text="Widget"; t.cell(1,1).text="10"; t.cell(2,0).text="Gadget"; t.cell(2,1).text="25"
    b = io.BytesIO(); d.save(b); return b.getvalue()
def xlsx_bytes():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sales"
    ws.append(["Region", "Q1", "Q2"]); ws.append(["North", 100, 150]); ws.append(["South", 80, 60]); ws.append(["Total", "=SUM(B2:B3)", "=SUM(C2:C3)"])
    b = io.BytesIO(); wb.save(b); return b.getvalue()
def pptx_bytes():
    from pptx import Presentation
    from pptx.util import Inches
    p = Presentation(); s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = "Roadmap"; s.placeholders[1].text = "Launch in March"
    s2 = p.slides.add_slide(p.slide_layouts[5]); s2.shapes.title.text = "Budget"
    tb = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table; tb.cell(0,0).text="Team"; tb.cell(0,1).text="Cost"; tb.cell(1,0).text="Eng"; tb.cell(1,1).text="500"
    s2.notes_slide.notes_text_frame.text = "Mention hiring"
    b = io.BytesIO(); p.save(b); return b.getvalue()
