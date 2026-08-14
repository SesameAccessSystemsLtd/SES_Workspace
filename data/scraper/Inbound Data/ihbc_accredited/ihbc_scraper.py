
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os
# from hubspot import HubSpot # Uncomment when ready to push to HubSpot

# --- Configuration ---
IHBC_URL = 'https://www.ihbc.org.uk/accredited/'
OUTPUT_CSV_PATH = 'ihbc_leads.csv'
# Replace with your actual HubSpot Private App Access Token or API Key
# HUBSPOT_API_KEY = 'YOUR_HUBSPOT_API_KEY_HERE' 

def fetch_and_parse_ihbc_data(url):
    """Fetches the IHBC accredited page and parses the data for accredited professionals."""
    print(f"Fetching data from: {url}")
    response = requests.get(url)
    response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
    soup = BeautifulSoup(response.text, 'html.parser')

    # --- New Strategy: Find HESPR links directly and inspect their parents ---
    hespr_links = soup.find_all('a', href=lambda href: href and 'hespr.org.uk' in href)

    if not hespr_links:
        raise ValueError("Could not find any HESPR profile links on the IHBC page. The structure may have changed or the links are not present as expected.")

    print(f"Found {len(hespr_links)} HESPR profile links. Inspecting surrounding HTML for the first 5:")
    data = []
    for i, link_tag in enumerate(hespr_links[:5]):
        print(f"\n--- HESPR Link {i} Found ---")
        print(f"Link: {link_tag['href']}")
        
        # Print the parent div/p/li/article to understand context around the link
        parent_element = link_tag.find_parent(['div', 'p', 'li', 'article', 'section'])
        if parent_element:
            print(f"Parent Element HTML (first 1500 chars):\n{str(parent_element)[:1500]}\n")
        else:
            print("No significant parent element (div, p, li, article, section) found nearby.")
        print("-" * 50 + "\n")
        
        # For now, we only extract the link and placeholders for other data
        data.append({
            'first_name': '', 
            'last_name': '',  
            'email': None, 
            'qualification': '', 
            'location': '', 
            'source': 'IHBC',
            'lead_score': 0, 
            'hespr_link': link_tag['href']
        })

    return data

def main():
    try:
        ihbc_data = fetch_and_parse_ihbc_data(IHBC_URL)
        df = pd.DataFrame(ihbc_data)

        # Save to CSV
        current_dir = os.path.dirname(os.path.abspath(__file__))
        output_csv_full_path = os.path.join(current_dir, OUTPUT_CSV_PATH)
        df.to_csv(output_csv_full_path, index=False)
        print(f"Successfully saved {len(df)} leads to {output_csv_full_path}")
        print("\n--- Data Preview (First 5 rows) ---")
        print(df.head().to_markdown(index=False)) # Display as markdown table

        # --- HubSpot Push (REQUIRES CONFIRMATION) ---
        print("\n--- HubSpot Push Section (SKIPPED - Awaiting Confirmation) ---")
        # if HUBSPOT_API_KEY != 'YOUR_HUBSPOT_API_KEY_HERE':
        #     client = HubSpot(api_key=HUBSPOT_API_KEY)
        #     leads_pushed = 0
        #     # Prepare for batch API push
        #     inputs = []
        #     for index, row in df.iterrows():
        #         properties = {
        #             'firstname': row['first_name'],
        #             'lastname': row['last_name'],
        #             'notes': f"Quals: {row['qualification']}, Branch: {row['location']}, Score: {row['lead_score']}, Source: IHBC, HESPR Link: {row['hespr_link']}"
        #             # Add other properties as needed, e.g., 'lead_score', 'source' as custom properties in HubSpot
        #             # 'lead_score': row['lead_score'], 
        #             # 'source': row['source']
        #         }
        #         inputs.append({'properties': properties})

        #         # Batch create every 100 leads or if it's the last one
        #         if len(inputs) == 100 or index == len(df) - 1:
        #             try:
        #                 print(f"Pushing {len(inputs)} contacts to HubSpot...")
        #                 # client.crm.contacts.batch_api.create(body={'inputs': inputs})
        #                 leads_pushed += len(inputs)
        #                 inputs = [] # Reset inputs for next batch
        #             except Exception as e:
        #                 print(f"Error pushing batch to HubSpot: {e}")
        #                 # Log error, potentially save failed leads
        #     print(f'Pulled {len(df)} leads, pushed {leads_pushed} to HubSpot. Check pipeline.')
        # else:
        #     print("HubSpot API Key not configured. Skipping HubSpot push.")

    except requests.exceptions.RequestException as e:
        print(f"Network or HTTP error: {e}")
    except ValueError as e:
        print(f"Data parsing error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
