import requests
from prettytable import PrettyTable

def read_api_keys_and_org_ids(file_path):
    with open(file_path, 'r') as file:
        line = file.readline().strip()
        api_key, org_id = line.split(',')
        return api_key, org_id

def get_organization_license_overview(api_key, org_id):
    url = f"https://api.meraki.com/api/v1/organizations/{org_id}/licenses/overview"
    headers = {
        'X-Cisco-Meraki-API-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error {response.status_code}: {response.text}")
        return None

def main():
    file_path = 'api_keys_org_ids.txt'
    api_key, org_id = read_api_keys_and_org_ids(file_path)
    license_overview = get_organization_license_overview(api_key, org_id)
    
    if license_overview is not None:
        print("License Overview:")
        #print(license_overview)  # Print the entire response for inspection
        
        # Create a table for the overall license info
        overview_table = PrettyTable()
        overview_table.field_names = ["Field", "Value"]
        overview_table.add_row(["Status", license_overview.get('status', 'N/A')])
        overview_table.add_row(["Expiration Date", license_overview.get('expirationDate', 'N/A')])
        
        print(overview_table)
        
        # Create a table for licensed device counts
        device_counts = license_overview.get('licensedDeviceCounts', {})
        device_table = PrettyTable()
        device_table.field_names = ["Device Type", "Count"]
        
        for device, count in device_counts.items():
            device_table.add_row([device, count])
        
        print(device_table)
        
    else:
        print("Failed to retrieve the license overview.")

if __name__ == "__main__":
    main()