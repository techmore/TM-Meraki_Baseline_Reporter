import requests
import json
from prettytable import PrettyTable

# Read API keys and organization IDs from the file
api_keys_org_ids_file = 'api_keys_org_ids.txt'
with open(api_keys_org_ids_file, 'r') as file:
    data = file.readline().strip().split(',')
    api_key = data[0]
    org_id = data[1]

# Define the base URL for the Meraki API
base_url = 'https://api.meraki.com/api/v1'

# Set up the headers for the API request
headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'X-Cisco-Meraki-API-Key': api_key
}

# Function to get organization information
def get_organization_info(org_id):
    url = f'{base_url}/organizations/{org_id}'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to get network list for an organization
def get_networks(org_id):
    url = f'{base_url}/organizations/{org_id}/networks'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to get device inventory for an organization
def get_device_inventory(org_id):
    url = f'{base_url}/organizations/{org_id}/inventory/devices'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to get MDM information for a network
def get_mdm_info(network_id):
    url = f'{base_url}/networks/{network_id}/sm/devices'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to get device profiles for a network
def get_device_profiles(network_id, device_id):
    url = f'{base_url}/networks/{network_id}/sm/devices/{device_id}/profiles'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to get device apps for a network
def get_device_apps(network_id, device_id):
    url = f'{base_url}/networks/{network_id}/sm/devices/{device_id}/apps'
    response = requests.get(url, headers=headers)
    return response.json()

# Function to create a summary report
def create_summary_report(org_info, networks, inventory):
    report = f"Summary Report:\n"
    report += f"Organization Name: {org_info.get('name', 'N/A')}\n"
    report += f"Total Networks: {len(networks)}\n"
    report += f"Total Devices: {len(inventory)}\n"
    return report

# Function to display networks in a table
def display_networks_table(networks):
    table = PrettyTable()
    table.field_names = ["Network ID", "Name", "Type", "Tags"]
    for network in networks:
        table.add_row([network.get('id', 'N/A'), network.get('name', 'N/A'), network.get('type', 'N/A'), ', '.join(network.get('tags', []))])
    print(table)

# Function to display device inventory in a table
def display_inventory_table(inventory):
    table = PrettyTable()
    table.field_names = ["Device ID", "Name", "Model", "Serial"]
    for device in inventory:
        table.add_row([device.get('id', 'N/A'), device.get('name', 'N/A'), device.get('model', 'N/A'), device.get('serial', 'N/A')])
    print(table)

# Function to display MDM information in a table
def display_mdm_info_table(mdm_info):
    if isinstance(mdm_info, list):
        table = PrettyTable()
        table.field_names = ["Device Name", "OS", "Serial", "Owner Email", "Model"]
        for device in mdm_info:
            table.add_row([device.get('name', 'N/A'), device.get('osName', 'N/A'), device.get('serialNumber', 'N/A'), device.get('ownerEmail', 'N/A'), device.get('model', 'N/A')])
        print(table)
    else:
        print("No MDM devices found or invalid data structure.")

# Function to summarize MDM information
def summarize_mdm_info(mdm_info):
    summary = {
        "new_clients": [],
        "old_clients": [],
        "platforms": {},
        "tags": {},
        "os_versions": {},
        "models": {},
        "home_apps": {},
        "unmonitored_apps": {}
    }
    if isinstance(mdm_info, list):
        for device in mdm_info:
            if device.get('isNewClient', False):
                summary["new_clients"].append(device)
            else:
                summary["old_clients"].append(device)

            platform = device.get('platform', 'N/A')
            if platform in summary["platforms"]:
                summary["platforms"][platform] += 1
            else:
                summary["platforms"][platform] = 1

            tags = device.get('tags', [])
            for tag in tags:
                if tag in summary["tags"]:
                    summary["tags"][tag] += 1
                else:
                    summary["tags"][tag] = 1

            os_version = device.get('osName', 'N/A')
            if os_version in summary["os_versions"]:
                summary["os_versions"][os_version] += 1
            else:
                summary["os_versions"][os_version] = 1

            model = device.get('model', 'N/A')
            if model in summary["models"]:
                summary["models"][model] += 1
            else:
                summary["models"][model] = 1

    return summary

# Function to display MDM summary information
def display_mdm_summary(summary):
    print("New Clients:")
    new_clients_table = PrettyTable()
    new_clients_table.field_names = ["Device Name", "OS", "Serial", "Owner Email"]
    for device in summary["new_clients"]:
        new_clients_table.add_row([device.get('name', 'N/A'), device.get('osName', 'N/A'), device.get('serialNumber', 'N/A'), device.get('ownerEmail', 'N/A')])
    print(new_clients_table)

    print("Old Clients:")
    old_clients_table = PrettyTable()
    old_clients_table.field_names = ["Device Name", "OS", "Serial", "Owner Email"]
    for device in summary["old_clients"]:
        old_clients_table.add_row([device.get('name', 'N/A'), device.get('osName', 'N/A'), device.get('serialNumber', 'N/A'), device.get('ownerEmail', 'N/A')])
    print(old_clients_table)

    print("Top Platforms:")
    platforms_table = PrettyTable()
    platforms_table.field_names = ["Platform", "Count"]
    for platform, count in summary["platforms"].items():
        platforms_table.add_row([platform, count])
    print(platforms_table)

    print("Top Tags:")
    tags_table = PrettyTable()
    tags_table.field_names = ["Tag", "Count"]
    for tag, count in summary["tags"].items():
        tags_table.add_row([tag, count])
    print(tags_table)

    print("Top Operating Systems:")
    os_versions_table = PrettyTable()
    os_versions_table.field_names = ["OS Version", "Count"]
    for os_version, count in summary["os_versions"].items():
        os_versions_table.add_row([os_version, count])
    print(os_versions_table)

    print("Top Models:")
    models_table = PrettyTable()
    models_table.field_names = ["Model", "Count"]
    for model, count in summary["models"].items():
        models_table.add_row([model, count])
    print(models_table)

# Function to display device profiles
def display_device_profiles(network_id, device_id):
    profiles = get_device_profiles(network_id, device_id)
    table = PrettyTable()
    table.field_names = ["Profile Name", "Status"]
    if isinstance(profiles, list):
        for profile in profiles:
            table.add_row([profile.get('name', 'N/A'), profile.get('status', 'N/A')])
    print(table)

# Function to display device apps
def display_device_apps(network_id, device_id):
    apps = get_device_apps(network_id, device_id)
    table = PrettyTable()
    table.field_names = ["App Name", "Version", "Status"]
    if isinstance(apps, list):
        for app in apps:
            table.add_row([app.get('name', 'N/A'), app.get('version', 'N/A'), app.get('status', 'N/A')])
    print(table)

# Main script logic
def main():
    org_info = get_organization_info(org_id)
    networks = get_networks(org_id)
    inventory = get_device_inventory(org_id)

    # Create and print summary report
    summary_report = create_summary_report(org_info, networks, inventory)
    print(summary_report)

    # Display networks table
    print("Networks:")
    display_networks_table(networks)

    # Display device inventory table
    print("\nDevice Inventory:")
    display_inventory_table(inventory)

    # Summarize and display MDM information for each network
    for network in networks:
        network_id = network['id']
        mdm_info = get_mdm_info(network_id)
        print(f"\nMDM Info for Network {network['name']} (ID: {network_id}):")
        display_mdm_info_table(mdm_info)

        # Summarize MDM info
        mdm_summary = summarize_mdm_info(mdm_info)
        display_mdm_summary(mdm_summary)

        # Display profiles and apps for each device
        #if isinstance(mdm_info, list):
        #    for device in mdm_info:
        #        device_id = device.get('id', 'N/A')
        #        if device_id != 'N/A':
        #            print(f"\nProfiles for Device {device_id}:")
        #            display_device_profiles(network_id, device_id)

        #            print(f"\nApps for Device {device_id}:")
        #            display_device_apps(network_id, device_id)

if __name__ == "__main__":
    main()
