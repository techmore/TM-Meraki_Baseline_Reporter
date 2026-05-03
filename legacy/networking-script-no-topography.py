import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
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

# Rate limiting parameters
RATE_LIMIT = 5  # Number of requests per second
lock = Lock()

# Rate limiting function
def rate_limited_request(request_func, *args, **kwargs):
    with lock:
        response = request_func(*args, **kwargs)
        time.sleep(1 / RATE_LIMIT)  # Simple rate limiting
    return response

# Function to get organization information
def get_organization_info(org_id):
    url = f'{base_url}/organizations/{org_id}'
    response = rate_limited_request(requests.get, url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: Could not retrieve organization info (Status Code: {response.status_code})")
        return None

# Function to get network list for an organization
def get_networks(org_id):
    url = f'{base_url}/organizations/{org_id}/networks'
    response = rate_limited_request(requests.get, url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: Could not retrieve networks (Status Code: {response.status_code})")
        return []

# Function to get device inventory for an organization
def get_device_inventory(org_id):
    url = f'{base_url}/organizations/{org_id}/inventory/devices'
    response = rate_limited_request(requests.get, url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: Could not retrieve device inventory (Status Code: {response.status_code})")
        return []

# Function to get all details for a specific device (with caching)
device_cache = {}
def get_device_details(network_id, device_id):
    if device_id in device_cache:
        return device_cache[device_id]
    url = f'{base_url}/networks/{network_id}/devices/{device_id}'
    response = rate_limited_request(requests.get, url, headers=headers)
    if response.status_code == 200:
        device_cache[device_id] = response.json()
        return device_cache[device_id]
    elif response.status_code == 404:
        return None  # Skip logging 404 errors to avoid cluttering output
    else:
        print(f"Error: Could not retrieve details for device {device_id} (Status Code: {response.status_code})")
        return None

# Multithreaded function to get device details for all devices
def get_all_device_details(devices):
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(get_device_details, device.get('networkId'), device['serial']) for device in devices if device.get('networkId')]
        results = []
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
        return results

# Function to get switch port statuses for a device
def get_switch_port_statuses(device_id):
    url = f'{base_url}/devices/{device_id}/switch/ports/statuses'
    response = rate_limited_request(requests.get, url, headers=headers)
    if response.status_code == 200:
        return {'device_id': device_id, 'ports': response.json()}
    else:
        print(f"Error: Could not retrieve switch port statuses for device {device_id} (Status Code: {response.status_code})")
        return {'device_id': device_id, 'ports': []}

# Multithreaded function to get switch port statuses for all switches
def get_all_switch_port_statuses(switches):
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(get_switch_port_statuses, switch['serial']) for switch in switches]
        results = []
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
        return results

# Main script
networks = get_networks(org_id)
devices = get_device_inventory(org_id)

if networks and devices:
    # Use multithreading to get device details
    all_device_details = get_all_device_details(devices)

    # Display networks table
    network_table = PrettyTable()
    network_table.field_names = ["Network ID", "Name", "Type", "Tags"]
    for network in networks:
        network_table.add_row([
            network.get('id', 'N/A'),
            network.get('name', 'N/A'),
            network.get('type', 'N/A'),
            ', '.join(network.get('tags', []) or ['N/A'])
        ])
    print("\nNetworks:")
    print(network_table)

    # Display devices table
    device_table = PrettyTable()
    device_table.field_names = ["Serial", "Name", "Model", "Network ID", "IP", "MAC"]
    for device in all_device_details:
        device_table.add_row([
            device.get('serial', 'N/A'),
            device.get('name', 'N/A'),
            device.get('model', 'N/A'),
            device.get('networkId', 'N/A'),
            device.get('lanIp', 'N/A'),
            device.get('mac', 'N/A'),
        ])
    print("\nDevices:")
    print(device_table)

    # Filter switches from devices
    switches = [device for device in all_device_details if device.get('model', '').startswith('MS')]

    if switches:
        # Get switch port statuses for all switches
        all_switch_ports = get_all_switch_port_statuses(switches)

        # Display switch port statuses with LLDP/CDP info
        for switch_ports in all_switch_ports:
            device_id = switch_ports['device_id']
            ports = switch_ports['ports']
            device_name = next((device['name'] for device in switches if device['serial'] == device_id), 'Unknown')
            print(f"\nSwitch Port Statuses for {device_name} ({device_id}):")
            port_table = PrettyTable()
            port_table.field_names = ["Port", "Status", "Enabled", "PoE", "Client", "LLDP/CDP Info"]
            for port in ports:
                lldp_info = port.get('lldp') or port.get('cdp')
                if lldp_info:
                    lldp_str = ', '.join([f"{k}: {v}" for k, v in lldp_info.items()])
                else:
                    lldp_str = 'N/A'
                port_table.add_row([
                    port.get('portId', 'N/A'),
                    port.get('status', 'N/A'),
                    port.get('enabled', 'N/A'),
                    port.get('powerUsageInWh', 'N/A'),
                    port.get('clientId', 'N/A'),
                    lldp_str
                ])
            print(port_table)
    else:
        print("No switches found in the device list.")
else:
    print("No networks or devices found.")
