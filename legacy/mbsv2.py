import requests
from prettytable import PrettyTable

# API endpoint for organizations
org_url = 'https://api.meraki.com/api/v1/organizations'

# API endpoints for network usage
net_usage_url = 'https://api.meraki.com/api/v1/organizations/{}/networks'

# API endpoint for devices
device_url = 'https://api.meraki.com/api/v1/networks/{}/devices'

# API endpoint for MDM
mdm_url = 'https://api.meraki.com/api/v1/networks/{}/sm/devices/'

# API key and org ID file
api_keys_org_ids_file = "api_keys_org_ids.txt"

# Read the file containing the API keys and org IDs
with open(api_keys_org_ids_file, "r") as f:
    lines = f.readlines()

# Create a table for organizational and security information
org_table = PrettyTable()
org_table.field_names = ["Organization", "Total Networks", "Total Devices", "Total APs", "Total Switches", "Top Switch Usage", "Top AP Usage"]
org_table.align = "l"

# Create a table for device inventory
device_table = PrettyTable()
device_table.field_names = ["Organization", "Network", "Device Model", "Firmware Version"]
device_table.align = "l"

# Create a table for MDM information
mdm_table = PrettyTable()
mdm_table.field_names = ["Organization", "Total MDM Devices", "Top Device Types", "Top Users", "Top Deployed Apps"]
mdm_table.align = "l"

def get_organization_name(api_key, org_id):
    response = requests.get(f"{org_url}/{org_id}", headers={'X-Cisco-Meraki-API-Key': api_key})
    if response.status_code == 200:
        return response.json().get('name', 'Unknown')
    return "Unknown"

def get_networks(api_key, org_id):
    response = requests.get(net_usage_url.format(org_id), headers={'X-Cisco-Meraki-API-Key': api_key})
    if response.status_code == 200:
        return response.json()
    return []

def get_device_usage(api_key, network_id):
    response = requests.get(device_url.format(network_id), headers={'X-Cisco-Meraki-API-Key': api_key})
    if response.status_code == 200:
        devices = response.json()
        total_devices = len(devices)
        ap_usage = sum(device['usage']['apUsage'] for device in devices if 'usage' in device and 'apUsage' in device['usage'])
        switch_usage = sum(device['usage']['switchUsage'] for device in devices if 'usage' in device and 'switchUsage' in device['usage'])
        return total_devices, ap_usage, switch_usage
    return 0, 0, 0

def get_mdm_info(api_key, network_id):
    response = requests.get(mdm_url.format(network_id), headers={'X-Cisco-Meraki-API-Key': api_key})
    if response.status_code == 200:
        mdm_devices = response.json()
        total_mdm_devices = len(mdm_devices)
        device_types = {}
        top_users = {}
        top_apps = {}
        for device in mdm_devices:
            device_type = device.get('deviceType', 'Unknown')
            device_types[device_type] = device_types.get(device_type, 0) + 1
            owner = device.get('ownerEmail', 'Unknown')
            top_users[owner] = top_users.get(owner, 0) + 1
            apps = device.get('installedApps', [])
            for app in apps:
                app_name = app.get('name', 'Unknown')
                top_apps[app_name] = top_apps.get(app_name, 0) + 1
        top_device_types = sorted(device_types.items(), key=lambda x: x[1], reverse=True)
        top_user_list = sorted(top_users.items(), key=lambda x: x[1], reverse=True)[:10]
        top_app_list = sorted(top_apps.items(), key=lambda x: x[1], reverse=True)[:10]
        return total_mdm_devices, top_device_types, top_user_list, top_app_list
    return 0, [], [], []

# Loop through each line in the file
for line in lines:
    api_key, org_id = line.strip().split(",")
    org_name = get_organization_name(api_key, org_id)
    networks = get_networks(api_key, org_id)
    
    total_networks = len(networks)
    total_devices = 0
    total_aps = 0
    total_switches = 0
    top_switch_usage = 0
    top_ap_usage = 0

    for network in networks:
        network_id = network['id']
        network_name = network['name']
        
        device_usage = get_device_usage(api_key, network_id)
        total_devices += device_usage[0]
        total_aps += device_usage[1]
        total_switches += device_usage[2]
        top_switch_usage = max(top_switch_usage, device_usage[2])
        top_ap_usage = max(top_ap_usage, device_usage[1])

        # Device inventory details
        response = requests.get(device_url.format(network_id), headers={'X-Cisco-Meraki-API-Key': api_key})
        if response.status_code == 200:
            devices = response.json()
            for device in devices:
                device_model = device.get('model', 'Unknown')
                firmware_version = device.get('firmware', 'Unknown')
                device_table.add_row([org_name, network_name, device_model, firmware_version])
    
    org_table.add_row([org_name, total_networks, total_devices, total_aps, total_switches, top_switch_usage, top_ap_usage])

    # MDM details
    for network in networks:
        network_id = network['id']
        mdm_info = get_mdm_info(api_key, network_id)
        total_mdm_devices, top_device_types, top_user_list, top_app_list = mdm_info

        top_device_types_str = ", ".join([f"{device_type}: {count}" for device_type, count in top_device_types])
        top_users_str = ", ".join([f"{user}: {count}" for user, count in top_user_list])
        top_apps_str = ", ".join([f"{app}: {count}" for app, count in top_app_list])

        mdm_table.add_row([org_name, total_mdm_devices, top_device_types_str, top_users_str, top_apps_str])

# Print the results
print("Organization and Security Information")
print(org_table)
print("\nDevice Inventory")
print(device_table)
print("\nMDM Information")
print(mdm_table)