"""Data update coordinator for Mannito Farming."""
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import API, Device, Sensor
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MannitoFarmingDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching data from the Mannito Farming controller."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """
        Initialize the coordinator.
        
        Args:
            hass: Home Assistant instance
            config_entry: Config entry containing configuration data

        """
        self.hass = hass
        self.config_entry = config_entry
        self.host = config_entry.data[CONF_HOST]
        self.username = config_entry.data.get(CONF_USERNAME)
        self.password = config_entry.data.get(CONF_PASSWORD)
        self.session = async_get_clientsession(hass)
        self.api = API(self.host, self.username, self.password, self.session)
        self._devices: dict[str, Device] = {}
        self._sensors: dict[str, Sensor] = {}
        self.device_info: dict[str, Any] = {}

        _LOGGER.debug("Initializing coordinator for host: %s", self.host)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.host}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Fetch data from the API.
        
        Returns:
            Dictionary with device states
        
        Raises:
            UpdateFailed: If the update fails

        """
        try:
            _LOGGER.info("Starting _async_update_data")
            # Load device info if not already loaded
            if not self.device_info:

                self.device_info = await self.api.get_device_info()
                _LOGGER.debug("Loaded Device info from api: %s", self.device_info)

            if not self._devices:
                # First run, discover all devices
                devices = await self.api.discover_devices()
                for device in devices:
                    self._devices[device.device_id] = device

            if not self._sensors:
                # First run, discover all devices
                sensors = await self.api.discover_sensors()
                for sensor in sensors:
                    self._sensors[sensor.sensor_id] = sensor            # Update all device states

            # Update all device states using bulk endpoint
            data = {}
            try:
                # Fetch all device states in one call
                bulk_response = await self.api.get_all_device_states()
                devices_data = bulk_response.get("devices", []) if bulk_response else []

                # First mark all devices as unavailable
                for device in self._devices.values():
                    device.available = False

                # Process bulk response and update device states
                for device_info in devices_data:
                    device_id = device_info.get("id")
                    if device_id and device_id in self._devices:
                        device = self._devices[device_id]

                        # Device responded, so it's available
                        device.available = True

                        # Update device state - checking both 'state' and level-based devices
                        if "state" in device_info:
                            device.state = device_info["state"]

                        # Handle powerlevel (speed) for devices that support it (like fans)
                        if "powerlevel" in device_info:
                            device.powerlevel = device_info["powerlevel"]

                        # Store state data in the data dict for entities to use
                        data[device_id] = {
                            "state": device_info.get("state", False),
                            "powerlevel": device_info.get("powerlevel"),
                            "unit": device_info.get("powerlevel_unit")
                        }

                # Log any devices that weren't in the bulk response
                for device_id, device in self._devices.items():
                    if not device.available:
                        _LOGGER.warning("Device %s is not responding, marking as unavailable", device_id)
                        data[device_id] = {}  # Empty data for unavailable device

            except Exception as err:
                # If bulk endpoint fails, mark all devices unavailable
                for device in self._devices.values():
                    device.available = False
                _LOGGER.error("Error updating device states via bulk endpoint: %s", err)
                raise UpdateFailed(f"Error updating device data: {err}")
            for sensor_id, sensor in self._sensors.items():
                try:
                    sensor_data = await self.api.get_sensor_state(sensor_id)
                    data[sensor_id] = sensor_data

                    _LOGGER.debug("Mannito-Sensor data: %s", sensor_data)

                    # If we got a valid response, the sensor is available
                    if sensor_data:
                        sensor.available = True
                        # Update stored sensor value
                        if "sensor_value" in sensor_data:
                            if sensor_data.get("is_valid"):
                                sensor.sensor_value = sensor_data["sensor_value"]
                    else:
                        # Empty response means the sensor is not available
                        sensor.available = False
                        _LOGGER.warning("Sensor %s is not responding, marking as unavailable", sensor_id)
                except Exception as err:
                    # If an error occurred, the sensor is not available
                    sensor.available = False
                    _LOGGER.error("Error updating sensor %s: %s", sensor_id, err)
                    raise UpdateFailed(f"Error updating data: {err}")

            return data
        except Exception as err:
            raise UpdateFailed(f"Error updating data: {err}")

    async def get_device(self, device_id: str) -> Device | None:
        """
        Get device information.
        
        Args:
            device_id: Device ID to get
            
        Returns:
            Device object if found, None otherwise

        """
        return self._devices.get(device_id)

    async def get_all_devices(self) -> list[Device]:
        """
        Get all devices.
        
        Returns:
            List of all devices

        """
        return list(self._devices.values())

    async def get_sensor(self, sensor_id: str) -> Sensor | None:
        """
        Get sensor information.
        
        Args:
            sensor_id: Sensor ID to get
            
        Returns:
            Sensor object if found, None otherwise

        """
        return self._sensors.get(sensor_id)

    async def get_all_sensors(self) -> list[Sensor]:
        """
        Get all sensors.
        
        Returns:
            List of all sensors

        """
        return list(self._sensors.values())

    async def async_set_device_state(self, device_id: str, state: bool) -> bool:
        """
        Set the state of a device.
        
        Args:
            device_id: Device ID to control
            state: State to set (True=on, False=off)
            
        Returns:
            True if successful, False otherwise

        """
        success = await self.api.set_device_state(device_id, state)
        if success and device_id in self._devices:
            self._devices[device_id].state = state
        return success

    async def async_set_fan_speed(self, device_id: str, speed: int) -> bool:
        """
        Set the speed of a fan.
        
        Args:
            device_id: Fan ID to control
            speed: Speed to set (0-100)
            
        Returns:
            True if successful, False otherwise

        """
        _LOGGER.debug("Setting fan speed for device %s to %d", device_id, speed)

        success = await self.api.set_fan_speed(device_id, speed)
        if success and device_id in self._devices:
            device = self._devices[device_id]
            if device.powerlevel is not None:  # Only update if it's a fan
                device.powerlevel = speed
        return success

    def get_device_info(self) -> DeviceInfo:
        """
        Fetch device information from the API.

        Returns:
            Dictionary containing device information.

        """
        api_device_info = self.device_info or {}
        if not self.device_info:
            # If device_info is not already loaded, fetch it
            api_device_info = self.api.fetch_device_config()
            _LOGGER.debug("Fetched Device info from api: %s", api_device_info)

        return DeviceInfo(
            identifiers={(DOMAIN, self.host)},
            name=api_device_info.get("name", f"Mannito Farming {self.host}"),
            manufacturer=api_device_info.get("manufacturer", "Mannito Farming"),
            model=api_device_info.get("model", "Mannito Device"),
            sw_version=api_device_info.get("sw_version", "Unknown"),
            hw_version=api_device_info.get("hw_version", "Unknown"),
            serial_number=api_device_info.get("serial_number", "Unknown"),
            configuration_url=api_device_info.get("configuration_url", f"http://{self.host}"),
        )


    async def discover_and_update_devices(self) -> None:
        """Discover devices and update the internal device list."""
        try:
            devices = await self.api.discover_devices()
            for device in devices:
                self._devices[device.device_id] = device

            sensors = await self.api.discover_sensors()
            for sensor in sensors:
                self._sensors[sensor.sensor_id] = sensor

        except Exception as err:
            _LOGGER.error("Error discovering devices and sensors: %s", err)
