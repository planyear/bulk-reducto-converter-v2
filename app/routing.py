from app.handlers import csv_, docx_, ocr, passthrough, plaintext, xlsx_

HANDLERS = {
    ".md":       passthrough.convert,
    ".markdown": passthrough.convert,
    ".txt":      plaintext.convert,
    ".csv":      csv_.convert,
    ".docx":     docx_.convert,
    ".xlsx":     xlsx_.convert,
    ".xlsm":     xlsx_.convert,
    ".pdf":      ocr.convert,
    ".png":      ocr.convert,
    ".jpg":      ocr.convert,
    ".jpeg":     ocr.convert,
    ".webp":     ocr.convert,
    ".tiff":     ocr.convert,
    ".tif":      ocr.convert,
}
