# ==============================================================
# PDF Invoice Parser
# ==============================================================
# . .\invoice-processor\config.ps1
# . .\invoice-processor\pdf-extract.ps1
# ==============================================================
# Extracts invoice number and total value from a PDF invoice file.
# Uses a system PDF-to-text approach; falls back to regex scanning.
# ==============================================================

# Extract text from a PDF file
function Get-PdfText {
    param([string]$PdfPath)

    if (-not (Test-Path $PdfPath)) {
        Write-Warning "PDF not found: $PdfPath"
        return ""
    }

    # Try using the CoWork OS parse_document approach by reading via .NET
    # We'll use a simple text extraction approach
    try {
        # Some PDFs have embedded text we can read directly
        $raw = [System.IO.File]::ReadAllText($PdfPath)
        return $raw
    } catch {
        Write-Warning "Could not read PDF: $($_.Exception.Message)"
        return ""
    }
}

# Extract invoice number from PDF text
function Extract-InvoiceNumber {
    param([string]$Text)

    # Common patterns for invoice numbers
    $patterns = @(
        'Invoice\s*(?:Number|No|#|Ref|Reference)?\s*[:.\-#]?\s*(\w[\w/\-]*)',
        'INVOICE\s*(?:NUMBER|NO|#|REF|REFERENCE)?\s*[:.\-#]?\s*(\w[\w/\-]*)',
        'Inv\s*(?:No|#)?\s*[:.\-#]?\s*(\w[\w/\-]*)',
        'INV\s*(?:NO|#)?\s*[:.\-#]?\s*(\d[\w/\-]*)'
    )

    foreach ($pattern in $patterns) {
        $match = [regex]::Match($Text, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            $invoiceNo = $match.Groups[1].Value.Trim()
            # Filter out very short or very long matches
            if ($invoiceNo.Length -ge 3 -and $invoiceNo.Length -le 30) {
                return $invoiceNo
            }
        }
    }

    # Fallback: look for INV- pattern
    $fallback = [regex]::Match($Text, '(INV[-_]\d+)', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($fallback.Success) {
        return $fallback.Groups[1].Value
    }

    return $null
}

# Extract total/invoice value from PDF text
function Extract-InvoiceValue {
    param([string]$Text)

    # Patterns for total/invoice value (GBP)
    $patterns = @(
        '(?:Total|TOTAL|Invoice Total|INVOICE TOTAL|Amount Due|AMOUNT DUE|Balance Due|Grand Total)\s*[:.\-#]?\s*(?:£|GBP|EUR)?\s*([\d,]+\.\d{2})',
        '(?:Total|TOTAL)\s*(?:\(GBP\)|\(EUR\))?\s*[:.\-#]?\s*(?:£|GBP|EUR)?\s*([\d,]+\.\d{2})',
        '(?:Net|NET)\s*(?:Total|TOTAL)?\s*[:.\-#]?\s*(?:£|GBP|EUR)?\s*([\d,]+\.\d{2})',
        '(?:Subtotal|SUBTOTAL)\s*[:.\-#]?\s*(?:£|GBP|EUR)?\s*([\d,]+\.\d{2})'
    )

    foreach ($pattern in $patterns) {
        $match = [regex]::Match($Text, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            return [decimal]($match.Groups[1].Value -replace ',', '')
        }
    }

    return $null
}

# Main function: parse an invoice PDF
function Parse-InvoicePdf {
    param([string]$PdfPath)

    Write-Host "  Parsing PDF: $PdfPath" -ForegroundColor Gray

    $text = Get-PdfText $PdfPath
    if ([string]::IsNullOrEmpty($text)) {
        Write-Warning "  PDF text extraction returned empty."
        return @{ invoice_number = $null; invoice_value = $null }
    }

    $invoiceNo = Extract-InvoiceNumber $text
    $invoiceVal = Extract-InvoiceValue $text

    Write-Host "  Extracted: Invoice#=$invoiceNo Value=£$invoiceVal" -ForegroundColor Gray

    return @{
        invoice_number = $invoiceNo
        invoice_value = $invoiceVal
    }
}

Write-Host "[OK] PDF extract helper loaded." -ForegroundColor Green
