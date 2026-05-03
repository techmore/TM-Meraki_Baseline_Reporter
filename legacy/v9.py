import meraki
from prettytable import PrettyTable
import concurrent.futures
import time

# Read API keys and organization IDs from the file
api_keys_org_ids_file = 'api_keys_org_ids.txt'
with open(api_keys_org_ids_file, 'r') as file:
    data = file.readline().strip().split(',')
    api_key = data[0]
    org_id = data[1]

# Initialize the Meraki Dashboard API session
dashboard = meraki.DashboardAPI(api_key, suppress_logging=True)

# Time periods in seconds
time_periods = {
    "24 hours": 24 * 3600,
    "5 days": 5 * 24 * 3600
}

# Rate limit settings
RATE_LIMIT = 5  # Number of API calls per second
API_CALL_INTERVAL = 1.0 / RATE_LIMIT

# Function to get raw data for a given device (AP or switch)
def get_raw_data(serial):
    try:
        time.sleep(API_CALL_INTERVAL)
        device = dashboard.devices.getDevice(serial)
        return device
    except Exception as e:
        print(f"Error getting raw data for device {serial}: {e}")
        return None

# Function to get and process port configuration details
def get_and_process_port_configuration(serial, device_name):
    port_data = []
    try:
        time.sleep(API_CALL_INTERVAL)
        ports = dashboard.switch.getDeviceSwitchPorts(serial)
        for port in ports:
            port_id = port.get('portId', 'N/A')
            speed = port.get('speed', 'N/A')
            vlan = port.get('vlan', 'N/A')
            power = port.get('powerMode', 'N/A')
            connected_device = port.get('lldp', {}).get('systemName', 'N/A') or port.get('cdp', {}).get('deviceId', 'N/A')

            # Fetch detailed port information if available
            time.sleep(API_CALL_INTERVAL)
            port_status = dashboard.switch.getDeviceSwitchPort(serial, port_id)
            # Extract additional details from port_status if needed

            port_data.append([device_name, port_id, speed, vlan, power, connected_device])
    except Exception as e:
        print(f"Error getting port configuration for switch {serial}: {e}")
    
    return port_data

# Function to get total usage for a switch (placeholder for actual implementation)
def get_total_usage_for_switch(serial):
    try:
        time.sleep(API_CALL_INTERVAL)
        total_usage = {'sent': 0, 'recv': 0}  # Replace with actual API call
        return total_usage
    except Exception as e:
        print(f"Error getting total usage for switch {serial}: {e}")
        return None

# Function to get usage history for clients of a given device (AP or switch) and sort by total usage
def get_sorted_client_usage_history(serial, device_name, device_type):
    usage_data = []
    try:
        for period_name, timespan in time_periods.items():
            time.sleep(API_CALL_INTERVAL)
            clients = dashboard.devices.getDeviceClients(serial=serial, timespan=timespan)
            for client in clients:
                sent_mb = round(client['usage']['sent'] / (1024 * 1024), 2) if 'usage' in client and 'sent' in client['usage'] else 0
                recv_mb = round(client['usage']['recv'] / (1024 * 1024), 2) if 'usage' in client and 'recv' in client['usage'] else 0
                total_usage_mb = sent_mb + recv_mb
                usage_data.append([period_name, device_name, client.get('description', 'N/A'), client['mac'], sent_mb, recv_mb, total_usage_mb])
        
        usage_data.sort(key=lambda x: x[6], reverse=True)
    except Exception as e:
        print(f"Error getting client usage history for device {serial}: {e}")
    
    return usage_data

# Function to process each device and gather data
def process_device(device):
    device_serial = device['serial']
    device_name = device.get('name', device_serial)
    device_model = device['model']
    
    if 'MR' in device_model:  # AP devices
        device_data = get_raw_data(device_serial)
        if device_data:
            print(f"Raw data for AP {device_name}: {device_data}")

        client_usage_data = get_sorted_client_usage_history(device_serial, device_name, 'AP')
        for row in client_usage_data:
            aps_clients_table.add_row(row)
            
    elif 'MS' in device_model:  # Switch devices
        device_data = get_raw_data(device_serial)
        if device_data:
            print(f"Raw data for Switch {device_name}: {device_data}")

        port_configuration = get_and_process_port_configuration(device_serial, device_name)
        for row in port_configuration:
            switch_ports_table.add_row(row)

        total_usage = get_total_usage_for_switch(device_serial)
        if total_usage:
            switches_clients_table.add_row(["Total", device_name, "-", "-", round(total_usage['sent'] / (1024 * 1024), 2), round(total_usage['recv'] / (1024 * 1024), 2), "-"])

        client_usage_data = get_sorted_client_usage_history(device_serial, device_name, 'Switch')
        for row in client_usage_data:
            switches_clients_table.add_row(row)

# Get list of all networks
networks = dashboard.organizations.getOrganizationNetworks(org_id)

# Prepare tables
switches_clients_table = PrettyTable()
switches_clients_table.field_names = ["Time Period", "Device Name", "Client Description", "MAC", "Sent (MB)", "Recv (MB)", "Total Usage (MB)"]

aps_clients_table = PrettyTable()
aps_clients_table.field_names = ["Time Period", "Device Name", "Client Description", "MAC", "Sent (MB)", "Recv (MB)", "Total Usage (MB)"]

switch_ports_table = PrettyTable()
switch_ports_table.field_names = ["Device Name", "Port ID", "Speed", "VLAN", "Power Mode", "Connected Device"]

# Iterate through each network to find devices
devices = []
for network in networks:
    network_id = network['id']
    devices.extend(dashboard.networks.getNetworkDevices(network_id))

# Use multithreading to process devices concurrently
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    executor.map(process_device, devices)

# Display tables
print("\nSwitches Clients Usage Summary:")
print(switches_clients_table)

print("\nAccess Points Clients Usage Summary:")
print(aps_clients_table)

print("\nSwitch Ports Configuration:")
print(switch_ports_table)