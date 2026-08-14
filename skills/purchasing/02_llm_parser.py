import os
import json
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env")

sys.path.insert(0, str(Path(__file__).parents[2] / "lib"))
from llm import complete

def parse_emails():
    
    input_file = r"C:\Users\GilesHugo\SES_Workspace\data\raw_purchasing_emails.json"
    output_file = r"C:\Users\GilesHugo\SES_Workspace\data\parsed_insights.json"
    
    try:
        with open(input_file, 'r') as f:
            emails = json.load(f)
    except Exception as e:
        print(f"Error reading {input_file}: {e}")
        return

    if not emails:
        print("No raw emails to process.")
        with open(output_file, 'w') as f:
            json.dump([], f)
        return

    insights = []
    
    system_instruction = """
    You are a professional procurement assistant.
    Analyze the provided email text (which may include extracted PDF text).
    Extract the following details:
    1. po_number: Look for the PO Number. It may be listed under 'Customer Order Number', 'Your Ref', or 'Buyer Ref'.
       Format: 5 digits starting with 2, always followed by a hyphen and a job number or date suffix (e.g. "28883-1555" or "28883-280521").
       Suppliers sometimes quote only the base number (e.g. "28883") — extract exactly what is visible; do NOT invent the suffix.
       REJECT any number with more than 12 characters total or fewer than 5 digits — these are client reference numbers, not Sesame POs.
       Return null if not found.
    2. total_price: Extract the Goods Value Ex-VAT. If you can only find a Gross Total inclusive of UK VAT, you MUST deduct 20% by dividing the total by 1.2. Never return the inclusive tax value. Return null if not found.
    3. delivery_date: The estimated delivery date/dispatch date (resolve terms like "tomorrow" against the email date). Format YYYY-MM-DD. Return null if not found.
    4. has_issues: Boolean (true/false). True if the supplier mentions holds, delays, alternative parts, wrong parts, or inability to supply.
    5. issue_summary: If has_issues is true, provide a 1-sentence summary of the problem. Otherwise, return null.
    6. sqr_number: The SQR reference number (e.g., "SQR-12345"). Return null if not found.
    7. document_type: Classify the email into one of these exact strings:
       - "quote": Supplier providing pricing or availability in response to an enquiry
       - "order_confirmation": Supplier acknowledging receipt of a purchase order
       - "invoice": Supplier requesting payment for goods or services
       - "dispatch_note": Supplier confirming goods have been dispatched or shipped
       - "update": Supplier providing a status update, product change, price change, or lead time change
       - "other": Anything else — alerts, newsletters, internal emails, unrelated correspondence
    8. line_items: Extract each individual line item visible in the email or attached PDF. Return as an array.
       Each element must be an object with these fields:
         - "part_ref": the supplier's part number or description (string, or null if not visible)
         - "qty": quantity ordered or quoted (integer, or null if not visible)
         - "unit_price": unit price ex-VAT (float, or null if not visible)
         - "notes": any additional context visible on this line — e.g. "price break at 24", "for stock",
                    "replacement for X", "lead time 6 weeks", "minimum order quantity". Use null if nothing notable.
       If no line items are visible (e.g. a plain text acknowledgement with no table), return an empty array [].
       Do NOT invent line items — only extract what is explicitly present in the text.

    Return exactly a JSON object, no markdown wrappers.
    """

    for email in emails:
        print(f"  → {email['subject'].encode('ascii', 'ignore').decode()}")
        
        email_date = email.get('date', '')
        email_subject = email.get('subject', '')
        prompt = f"Email Date: {email_date}\nEmail Subject: {email_subject}\n\nEmail Text/PDF Combined:\n{email.get('full_text', '')}"
        
        try:
            raw = complete(
                prompt=prompt,
                system=system_instruction,
                max_tokens=2048,
                temperature=0.1,
            )
            result = json.loads(raw)

            insights.append({
                "email_id": email["id"],
                "subject": email["subject"],
                "sender": email["sender"],
                "receivedDateTime": email_date,
                "insights": result
            })

        except Exception as e:
            print(f"Error parsing email {email['id']}: {e}")
            
    with open(output_file, 'w') as f:
        json.dump(insights, f, indent=2)
    print(f"Parsed insights saved to {output_file}")

if __name__ == '__main__':
    parse_emails()
