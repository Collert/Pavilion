from django.test import TestCase
from pos_server.models import Station, Dish, Order, Menu


class StationModelTests(TestCase):
    """Tests for the Station model."""
    
    def test_station_creation(self):
        """Test that a station can be created with all required fields."""
        station = Station.objects.create(
            friendly_name="Test Kitchen",
            code="test_kitchen",
            icon="restaurant"
        )
        self.assertEqual(station.friendly_name, "Test Kitchen")
        self.assertEqual(station.code, "test_kitchen")
        self.assertEqual(station.icon, "restaurant")
    
    def test_station_str(self):
        """Test the string representation of a station."""
        station = Station.objects.create(
            friendly_name="Test Bar",
            code="test_bar",
            icon="local_cafe"
        )
        self.assertEqual(str(station), "Test Bar")
    
    def test_station_code_unique(self):
        """Test that station codes must be unique."""
        Station.objects.create(
            friendly_name="Station 1",
            code="unique_code",
            icon="restaurant"
        )
        with self.assertRaises(Exception):
            Station.objects.create(
                friendly_name="Station 2",
                code="unique_code",  # Same code
                icon="local_cafe"
            )
    
    def test_get_or_create_default_stations(self):
        """Test that default stations can be created."""
        result = Station.get_or_create_default_stations()
        self.assertIn("kitchen", result)
        self.assertIn("bar", result)
        self.assertIn("gng", result)
        self.assertEqual(result["kitchen"].code, "kitchen")
        self.assertEqual(result["bar"].code, "bar")
        self.assertEqual(result["gng"].code, "gng")


class DishStationTests(TestCase):
    """Tests for Dish station functionality."""
    
    def setUp(self):
        """Set up test data."""
        self.menu = Menu.objects.create(title="Test Menu", is_active=True)
        self.station = Station.objects.create(
            friendly_name="Test Station",
            code="test_station",
            icon="restaurant"
        )
    
    def test_dish_with_new_station(self):
        """Test that a dish can be created with a new_station."""
        dish = Dish.objects.create(
            title="Test Dish",
            price=10.00,
            station="kitchen",  # Legacy field
            new_station=self.station
        )
        dish.menu.add(self.menu)
        self.assertEqual(dish.new_station, self.station)
        self.assertEqual(dish.station_code, "test_station")
    
    def test_dish_station_code_with_legacy(self):
        """Test that station_code falls back to legacy station field."""
        dish = Dish.objects.create(
            title="Test Dish Legacy",
            price=10.00,
            station="bar",  # Legacy field only
            new_station=None
        )
        dish.menu.add(self.menu)
        self.assertEqual(dish.station_code, "bar")
    
    def test_dish_effective_station(self):
        """Test the effective_station property."""
        dish = Dish.objects.create(
            title="Test Dish",
            price=10.00,
            station="kitchen",
            new_station=self.station
        )
        dish.menu.add(self.menu)
        self.assertEqual(dish.effective_station, self.station)
    
    def test_dish_serialize_with_options_station(self):
        """Test that serialize_with_options includes station info."""
        dish = Dish.objects.create(
            title="Test Dish Serialize",
            price=10.00,
            station="kitchen",
            new_station=self.station
        )
        dish.menu.add(self.menu)
        serialized = dish.serialize_with_options()
        self.assertEqual(serialized["fields"]["station"], "test_station")
        self.assertEqual(serialized["fields"]["station_name"], "Test Station")
        self.assertEqual(serialized["fields"]["station_icon"], "restaurant")


class OrderStationStatusTests(TestCase):
    """Tests for Order station status functionality."""
    
    def setUp(self):
        """Set up test data."""
        self.station1 = Station.objects.create(
            friendly_name="Station 1",
            code="station1",
            icon="restaurant"
        )
        self.station2 = Station.objects.create(
            friendly_name="Station 2",
            code="station2",
            icon="local_cafe"
        )
        self.order = Order.objects.create(channel="store")
    
    def test_set_and_get_station_status(self):
        """Test setting and getting station status."""
        self.order.set_station_status(self.station1, 0)  # Pending
        self.assertEqual(self.order.get_station_status(self.station1), 0)
        
        self.order.set_station_status(self.station1, 1)  # Approved
        self.assertEqual(self.order.get_station_status(self.station1), 1)
        
        self.order.set_station_status(self.station1, 2)  # Completed
        self.assertEqual(self.order.get_station_status(self.station1), 2)
    
    def test_get_station_status_by_code(self):
        """Test getting station status by code string."""
        self.order.set_station_status(self.station1, 1)
        self.assertEqual(self.order.get_station_status("station1"), 1)
    
    def test_get_all_station_statuses(self):
        """Test getting all station statuses."""
        self.order.set_station_status(self.station1, 1)
        self.order.set_station_status(self.station2, 2)
        
        statuses = self.order.get_all_station_statuses()
        self.assertEqual(statuses["station1"], 1)
        self.assertEqual(statuses["station2"], 2)
    
    def test_all_stations_complete(self):
        """Test all_stations_complete method."""
        # Initially should be complete (no stations assigned)
        self.assertTrue(self.order.all_stations_complete())
        
        # Add a pending station
        self.order.set_station_status(self.station1, 0)
        self.assertFalse(self.order.all_stations_complete())
        
        # Complete the station
        self.order.set_station_status(self.station1, 2)
        self.assertTrue(self.order.all_stations_complete())
    
    def test_any_station_pending(self):
        """Test any_station_pending method."""
        self.assertFalse(self.order.any_station_pending())
        
        self.order.set_station_status(self.station1, 0)
        self.assertTrue(self.order.any_station_pending())
        
        self.order.set_station_status(self.station1, 1)
        self.assertFalse(self.order.any_station_pending())
    
    def test_any_station_in_progress(self):
        """Test any_station_in_progress method."""
        self.assertFalse(self.order.any_station_in_progress())
        
        self.order.set_station_status(self.station1, 1)
        self.assertTrue(self.order.any_station_in_progress())
        
        self.order.set_station_status(self.station1, 2)
        self.assertFalse(self.order.any_station_in_progress())
    
    def test_get_required_stations(self):
        """Test get_required_stations method."""
        self.order.set_station_status(self.station1, 1)
        self.order.set_station_status(self.station2, 2)
        
        required = self.order.get_required_stations()
        self.assertIn(self.station1, required)
        self.assertIn(self.station2, required)
    
    def test_legacy_compatibility(self):
        """Test that legacy station statuses still work."""
        self.order.kitchen_status = 0
        self.order.save()
        
        self.assertTrue(self.order.any_station_pending())
        self.assertFalse(self.order.all_stations_complete())
        
        self.order.kitchen_status = 2
        self.order.save()
        
        self.assertFalse(self.order.any_station_pending())
        self.assertTrue(self.order.all_stations_complete())
