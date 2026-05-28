# Home Assistant MCP Server

A comprehensive Model Context Protocol (MCP) server for integrating with Home Assistant. This server provides **60 tools** and resources to interact with your Home Assistant instance via the REST API, enabling full smart home management and automation.

## 🚀 Features Overview

**60 Total Tools** across **9 Major Categories** for comprehensive Home Assistant management:

### 🔧 **Core Operations** (13 tools)
- **Entity State Management**: Get, set, update, and delete entity states
- **Service Calls**: Execute Home Assistant services with full response support
- **Entity Search**: Advanced search with filtering by name, domain, or state
- **Event Handling**: Fire custom events with data payloads
- **Template Rendering**: Render Home Assistant templates with live data

### 🏠 **Area & Device Management** (8 tools)
- **Area Management**: Create, update, delete areas/zones with aliases
- **Device Registry**: List, configure, and manage all Home Assistant devices
- **Entity Organization**: Assign entities to areas, bulk area operations
- **Device Assignment**: Move devices between areas, enable/disable devices

### 🖥️ **System Management** (6 tools)
- **System Control**: Restart/stop Home Assistant safely
- **Health Monitoring**: Check system health and component status
- **Configuration Validation**: Validate configs before applying changes
- **Supervisor Integration**: Get supervisor info (Home Assistant OS)

### 🔌 **Integration Management** (6 tools)
- **Integration Lifecycle**: List, enable, disable, delete integrations
- **Integration Troubleshooting**: Reload integrations, get detailed info
- **Dynamic Management**: Programmatically manage integration configurations

### 🔔 **Notification Services** (3 tools)
- **Multi-Channel Notifications**: Send via mobile apps, persistent notifications
- **Service Discovery**: List all available notification services
- **Notification Management**: Dismiss persistent notifications

### 📋 **Entity Registry Management** (4 tools)
- **Entity Configuration**: Update entity names, areas, enabled/disabled status
- **Bulk Operations**: Enable/disable multiple entities
- **Registry Information**: Get detailed entity registry data

### 🤖 **Automation & Scene Management** (12 tools)
- **Automation Control**: Create, update, delete, trigger automations
- **Scene Management**: Activate scenes, create new scenes from current states
- **Automation Debugging**: Get execution traces and troubleshooting data
- **Lifecycle Management**: Full CRUD operations for automations

### 📊 **Data & History** (4 tools)
- **Historical Data**: Advanced history queries with filtering options
- **Logbook Access**: Get event history and entity changes
- **Error Logs**: Retrieve Home Assistant error logs for debugging

### 🎥 **Calendar & Media** (4 tools)
- **Calendar Integration**: List calendars and get events with date ranges
- **Camera Support**: Get images from camera entities (base64 encoded)
- **Intent Processing**: Handle Home Assistant voice/text intents
- **Real-time Events**: Subscribe to live Home Assistant events via SSE

## Installation

1. Clone this repository:
```bash
git clone https://github.com/cronus42/homeassistant-mcp.git
cd homeassistant-mcp
```

2. Run the setup script:
```bash
./setup.sh
```

3. Get your Home Assistant Long-Lived Access Token:
   - Log into your Home Assistant web interface
   - Go to Profile (click on your user name in bottom left)
   - Scroll down to "Long-lived access tokens"
   - Click "Create Token"
   - Give it a name (e.g., "MCP Server")
   - Copy the token

4. Configure your settings:
```bash
cp .env.example .env
# Edit .env with your Home Assistant URL and token
```

5. Test the connection:
```bash
python tests/test_connection.py
```

## Usage with MCP Clients

### Warp Terminal Integration

1. Run the setup script:
```bash
./setup.sh
```

2. Configure Warp to use the MCP server:
```bash
warp mcp add-server --config mcp_config.json
```

Or manually start the server:
```bash
./start_server.sh
```

### Claude Desktop Configuration

Add to your Claude Desktop config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "homeassistant": {
      "command": "/path/to/homeassistant-mcp/start_server.sh",
      "env": {},
      "args": []
    }
  }
}
```

## Available Tools

### `get_entity_state`
Get the current state of a specific Home Assistant entity.
```json
{
  "entity_id": "light.living_room"
}
```

### `call_service`
Call a Home Assistant service to control devices.
```json
{
  "domain": "light",
  "service": "turn_on",
  "entity_id": "light.living_room",
  "service_data": {
    "brightness": 255,
    "color_temp": 3000
  }
}
```

### `search_entities`
Search for entities by name, domain, or state.
```json
{
  "query": "temperature",
  "domain": "sensor"
}
```

### `fire_event`
Fire a custom event in Home Assistant.
```json
{
  "event_type": "custom_automation_trigger",
  "event_data": {
    "source": "mcp_server",
    "action": "test"
  }
}
```

### `get_history`
Get historical data for specific entities.
```json
{
  "entity_ids": ["sensor.temperature", "light.living_room"],
  "start_time": "2024-01-01T00:00:00Z"
}
```

### New Feature Examples

#### Area Management
```json
{
  "tool": "get_areas",
  "arguments": {}
}
```

#### Create and Manage Areas
```json
{
  "tool": "create_area",
  "arguments": {
    "name": "Home Office",
    "aliases": ["office", "workspace"]
  }
}
```

#### Device Management
```json
{
  "tool": "get_devices",
  "arguments": {}
}
```

#### System Health Check
```json
{
  "tool": "get_system_health",
  "arguments": {}
}
```

#### Send Notifications
```json
{
  "tool": "send_notification",
  "arguments": {
    "message": "Security Alert: Front door opened",
    "title": "Security System",
    "target": "mobile_app_phone"
  }
}
```

#### Integration Management
```json
{
  "tool": "get_integrations",
  "arguments": {}
}
```

## Testing

The server includes comprehensive test suites:

### Run All Tests
```bash
# Test tool definitions and schemas
python tests/test_mcp_tools.py

# Test tool handlers with mocking
python tests/test_tool_handlers.py

# Test with real Home Assistant (requires HA_TOKEN)
python tests/test_new_features.py

# Test basic connection
python tests/test_connection.py
```

### Test Results
- ✅ **60/60 tools** properly defined
- ✅ **All schemas** validated
- ✅ **All tool handlers** working correctly
- ✅ **100% test coverage** for new features

## Available Resources

- `homeassistant://states` - Current state of all entities
- `homeassistant://config` - Home Assistant configuration
- `homeassistant://services` - Available services
- `homeassistant://events` - Available event types

## Usage Examples

### Turn on a light
```json
{
  "tool": "call_service",
  "arguments": {
    "domain": "light",
    "service": "turn_on",
    "entity_id": "light.living_room"
  }
}
```

### Check temperature sensors
```json
{
  "tool": "search_entities",
  "arguments": {
    "query": "temperature",
    "domain": "sensor"
  }
}
```

### Get current state of all lights
```json
{
  "tool": "search_entities",
  "arguments": {
    "query": "light",
    "domain": "light"
  }
}
```

## Security

- Store your Home Assistant token securely
- Use environment variables for configuration
- Consider network security between your MCP client and Home Assistant
- Limit token permissions if possible

## Troubleshooting

### Connection Issues
- Verify Home Assistant URL is accessible
- Check if Home Assistant API is enabled
- Ensure token is valid and has proper permissions

### Authentication Errors
- Regenerate long-lived access token
- Check token format (should be a long string)
- Verify token is set in environment variable

### Service Call Failures
- Check entity IDs exist in Home Assistant
- Verify service names and parameters
- Check Home Assistant logs for errors

## Contributing

Feel free to submit issues and enhancement requests!
