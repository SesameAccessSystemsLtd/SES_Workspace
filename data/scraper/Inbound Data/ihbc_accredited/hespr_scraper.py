
from bs4 import BeautifulSoup
import pandas as pd
import os
import re
import html
from urllib.parse import urlparse, urlunparse, unquote
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# --- Configuration ---
URLS_CSV_PATH = 'C:\\Users\\GilesHugo\\OneDrive - SESAME ACCESS AZURE\\IHBC_Scraped_Raw_URLS.csv'
OUTPUT_DB_PATH = os.path.join(os.path.dirname(__file__), 'ihbc_hespr_contacts.csv')

def scrape_hespr_profile(url):
    """Scrapes an individual HESPR profile page for contact details and context."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as response:
            html_content = response.read()
        soup = BeautifulSoup(html_content, 'html.parser')

        name = ''
        job_title = ''
        tel = ''
        email = ''
        context = [] # To capture project context / people involved

        # Find Name - usually prominent, e.g., in an H1, H2, or strong tag at the top
        name_tag = soup.find(['h1', 'h2', 'strong'], class_='profile-name') or soup.find(['h1', 'h2', 'strong']) # Broader search
        if name_tag:
            name = name_tag.get_text(strip=True)

        # Find Email - looking for mailto: links or fa-envelope icon
        email_link = soup.find('a', href=lambda href: href and 'mailto:' in href)
        if email_link:
            email = email_link['href'].replace('mailto:', '')
        else:
            # Look for fa-envelope icon and then try to extract text next to it
            envelope_icon = soup.find('span', class_='fa fa-envelope')
            if envelope_icon and envelope_icon.next_sibling:
                # Try to get the text of the next sibling, or parent text
                email = envelope_icon.next_sibling.get_text(strip=True) if envelope_icon.next_sibling.name else envelope_icon.next_sibling.strip()
                # Basic email validation regex
                if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                    email = '' # Clear if not a valid email format

        # Refined extraction for job_title and tel using regex cleaning
        for label_text, field_name in [("Job title:", "job_title"), ("Tel:", "tel")]: # Email handled separately with mailto/icon logic
            label_tag = soup.find(lambda tag: tag.name in ['p', 'div', 'li'] and label_text in tag.get_text())
            if label_tag:
                full_text_from_tag = label_tag.get_text(strip=True)
                # Attempt to get the text immediately following the label
                match = re.search(f"{re.escape(label_text)}\\s*(.+)", full_text_from_tag, re.DOTALL) # Use DOTALL to match across lines
                if match:
                    extracted_value = match.group(1).strip()

                    if field_name == "job_title":
                        # Try to find the job title by looking for text before common delimiters
                        # These delimiters mark the start of other information, not part of the job title
                        match_job_title = re.match(r'(.+?)(?:Tel:|Email:|Which of the following roles|Historic Building conservation|Conservation planning|Architectural design|Historical research|Historic area assessments|Project Management|Archaeology|Other|$)', extracted_value, re.IGNORECASE | re.DOTALL)
                        if match_job_title:
                            job_title = match_job_title.group(1).strip()
                        else:
                            job_title = extracted_value.strip() # Fallback if no delimiter found
                        # Further cleanup for any residual email or phone patterns directly after job title
                        job_title = re.sub(r'\\b\\d{5,}\\s*\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}\\b|[^@]+@[^@]+\\.[^@]+\\b', '', job_title).strip()

                    elif field_name == "tel":
                        # Extract only phone numbers using a robust regex pattern
                        # This regex attempts to capture common UK phone number formats
                        phone_matches = re.findall(r'\\b(?:\\(?0\\d{4}\\)?\\s*\\d{6}|\\(?0\\d{3}\\)?\\s*\\d{3}\\s*\\d{4}|\\(?0\\d{2}\\)?\\s*\\d{4}\\s*\\d{4}|\\(?01\\d{9}|\\+44\\s?\\(?\\d{2,4}\\)?\\s?\\d{3,4}\\s?\\d{3,4}|\\(?07\\d{3}\\)?\\s*\\d{6}|\\(?08\\d{9}|\\(?02\\d{9}|\\(?03\\d{9})\\b', extracted_value)
                        tel = ', '.join(phone_matches).strip()
                        # If multiple numbers are found, join them. If none, it will be empty.

        # Context: Look for common section headers or descriptive paragraphs
        # This is highly dependent on HESPR page structure, so will start broad
        # and refine. Look for paragraphs or list items that don't look like contact info
        
        # Example: looking for paragraphs not part of the contact block
        for p_tag in soup.find_all('p'):
            p_text = p_tag.get_text(strip=True)
            if p_text and not any(kw in p_text for kw in ["Email:", "Tel:", "Name:", "Job title:"]):
                context.append(p_text)
        
        # Limit context to a reasonable length
        full_context = " ".join(context[:5]) # Join first 5 relevant paragraphs

        return {
            'url': url,
            'name': name,
            'job_title': job_title,
            'tel': tel,
            'email': email,
            'context': full_context,
            'has_contact_details': bool(email or tel)
        }
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return {
            'url': url,
            'name': '', 'job_title': '', 'tel': '', 'email': '', 'context': f'Error: {e}',
            'has_contact_details': False
        }
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return {
            'url': url,
            'name': '', 'job_title': '', 'tel': '', 'email': '', 'context': f'Error: {e}',
            'has_contact_details': False
        }

def main():
    if not os.path.exists(URLS_CSV_PATH):
        print(f"Error: URLS CSV file not found at {URLS_CSV_PATH}")
        return

    urls_df = pd.read_csv(URLS_CSV_PATH)
    
    # Rename column if necessary to 'url'
    if 'url' not in urls_df.columns:
        # Assuming the second column contains the URL if 'url' is not directly a header
        if len(urls_df.columns) > 1:
            urls_df = urls_df.rename(columns={urls_df.columns[1]: 'url'})
        else:
            print("Error: 'url' column not found and cannot infer URL column from CSV.")
            return

    # --- URL Pre-processing ---
    # 1. Drop rows where 'url' is NaN or empty string
    urls_df['url'] = urls_df['url'].astype(str) # Ensure URL column is string type
    urls_df.replace('nan', pd.NA, inplace=True) # Replace string 'nan' with actual NaN
    urls_df.dropna(subset=['url'], inplace=True)
    
    # 2. Clean URLs: URL-decode path, strip HTML tags, unescape entities, and ensure valid scheme
    
    cleaned_urls = []
    for original_url in urls_df['url']:
        # URL-decode the path segment first to reveal any encoded HTML tags
        parsed_url = urlparse(original_url)
        decoded_path = unquote(parsed_url.path)
        
        # Strip any HTML tags from the decoded path
        stripped_path = re.sub(r'<[^>]*>', '', decoded_path) # Remove <tag> and </tag>
        
        # Re-assemble the URL with the cleaned path
        temp_cleaned_url = parsed_url._replace(path=stripped_path).geturl()

        # Then, unescape HTML entities in the entire URL string (e.g., &amp; -> &)
        unescaped_url = html.unescape(temp_cleaned_url)
        
        # Ensure valid scheme (already handled, but re-checking for robustness)
        final_parsed_url = urlparse(unescaped_url)
        if not final_parsed_url.scheme: # If no scheme, default to https
            final_parsed_url = final_parsed_url._replace(scheme='https')
        
        cleaned_urls.append(urlunparse(final_parsed_url))
    urls_df['url'] = cleaned_urls
    # --------------------------

    if urls_df['url'].empty:
        print("No URLs found in the provided CSV for scraping.")
        return

    print(f"Found {len(urls_df)} URLs to scrape. Starting HESPR profile scraping...")
    
    all_contacts_data = []
    for index, row in urls_df.iterrows():
        profile_url = row['url']
        print(f"Scraping: {profile_url}")
        contact_info = scrape_hespr_profile(profile_url)
        all_contacts_data.append(contact_info)

    results_df = pd.DataFrame(all_contacts_data)
    results_df.to_csv(OUTPUT_DB_PATH, index=False)
    print(f"Scraping complete. Results saved to {OUTPUT_DB_PATH}")

    # Display contacts with details and those without
    contacts_with_details = results_df[results_df['has_contact_details']]
    contacts_without_details = results_df[~results_df['has_contact_details']]

    print("\\n--- Contacts with Details (First 5) ---\\n")
    if not contacts_with_details.empty:
        print(contacts_with_details.head().to_markdown(index=False))
    else:
        print("No contacts with details found.")

    print("\\n--- Contacts without Details (First 5) ---\\n")
    if not contacts_without_details.empty:
        print(contacts_without_details.head().to_markdown(index=False))
    else:
        print("All found contacts have details, or no contacts were processed.")

if __name__ == "__main__":
    main()
