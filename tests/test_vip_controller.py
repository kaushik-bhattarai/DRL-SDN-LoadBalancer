#!/usr/bin/env python3
"""
Test VIP Controller - Verify VIP Load Balancing Works

This script tests:
1. Controller is running with VIP support
2. VIP packets are intercepted
3. DNAT flows are installed
4. Servers receive requests via VIP
5. Load balancing decisions are made

Run this BEFORE full training to catch issues early!
"""

import requests
import time
import sys
import subprocess
from mininet_topology import start_network

RYU_URL = 'http://127.0.0.1:8080/sdrlb'

class VIPControllerTester:
    """Test suite for VIP controller"""
    
    def __init__(self):
        self.tests_passed = 0
        self.tests_failed = 0
        self.net = None
    
    def print_header(self, text):
        """Print test section header"""
        print("\n" + "="*70)
        print(f"  {text}")
        print("="*70)
    
    def print_test(self, name, passed, details=""):
        """Print test result"""
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
        if details:
            print(f"       {details}")
        
        if passed:
            self.tests_passed += 1
        else:
            self.tests_failed += 1
    
    def test_controller_running(self):
        """Test 1: Verify controller is running"""
        self.print_header("TEST 1: Controller Running")
        
        try:
            response = requests.get(f'{RYU_URL}/ports/200', timeout=2)
            self.print_test("Controller accessible", response.status_code == 200)
            return True
        except Exception as e:
            self.print_test("Controller accessible", False, str(e))
            return False
    
    def test_vip_endpoint(self):
        """Test 2: Verify VIP-specific endpoint exists"""
        self.print_header("TEST 2: VIP Endpoint Available")
        
        try:
            response = requests.get(f'{RYU_URL}/vip/stats', timeout=2)
            
            if response.status_code == 200:
                stats = response.json()
                self.print_test("VIP stats endpoint exists", True, 
                               f"Total requests: {stats.get('total_requests', 0)}")
                return True
            else:
                self.print_test("VIP stats endpoint exists", False, 
                               f"Status: {response.status_code}")
                return False
                
        except Exception as e:
            self.print_test("VIP stats endpoint exists", False, str(e))
            return False
    
    def test_network_setup(self):
        """Test 3: Start Mininet and verify connectivity"""
        self.print_header("TEST 3: Network Setup")
        
        try:
            print("Starting Mininet network...")
            self.net = start_network()
            time.sleep(3)
            
            self.print_test("Mininet network started", True)
            
            # Verify hosts exist
            h1 = self.net.get('h1')
            h2 = self.net.get('h2')
            h4 = self.net.get('h4')
            
            hosts_exist = all([h1, h2, h4])
            self.print_test("Required hosts exist (h1, h2, h4)", hosts_exist)
            
            if not hosts_exist:
                return False
            
            # Check IPs
            print(f"  h1 IP: {h1.IP()}")
            print(f"  h2 IP: {h2.IP()}")
            print(f"  h4 IP: {h4.IP()}")
            
            return True
            
        except Exception as e:
            self.print_test("Network setup", False, str(e))
            return False
    
    def test_http_servers(self):
        """Test 4: Start HTTP servers and verify they work"""
        self.print_header("TEST 4: HTTP Servers")
        
        if not self.net:
            self.print_test("HTTP servers", False, "Network not started")
            return False
        
        try:
            # Start servers on h1, h2, h3
            for host_name in ['h1', 'h2', 'h3']:
                host = self.net.get(host_name)
                
                # Create index file
                content = f'<html><body><h1>Server: {host_name}</h1></body></html>'
                host.cmd(f'mkdir -p /tmp/{host_name}')
                host.cmd(f'echo "{content}" > /tmp/{host_name}/index.html')
                
                # Start HTTP server
                host.cmd(f'cd /tmp/{host_name} && python3 -m http.server 80 > /dev/null 2>&1 &')
                print(f"  Started HTTP server on {host_name} ({host.IP()})")
            
            time.sleep(2)
            
            # Test localhost access
            all_working = True
            for host_name in ['h1', 'h2', 'h3']:
                host = self.net.get(host_name)
                result = host.cmd('curl -s -m 2 http://127.0.0.1/')
                
                if host_name in result:
                    self.print_test(f"{host_name} localhost test", True)
                else:
                    self.print_test(f"{host_name} localhost test", False, 
                                   f"Response: {result[:50]}")
                    all_working = False
            
            return all_working
            
        except Exception as e:
            self.print_test("HTTP servers", False, str(e))
            return False
    
    def test_basic_routing(self):
        """Test 5: Verify basic routing works (h2 -> h1)"""
        self.print_header("TEST 5: Basic Routing")
        
        if not self.net:
            self.print_test("Basic routing", False, "Network not started")
            return False
        
        try:
            h2 = self.net.get('h2')
            h1 = self.net.get('h1')
            
            # Test direct connection (same switch)
            result = h2.cmd(f'curl -s -m 2 http://{h1.IP()}/')
            
            if 'h1' in result:
                self.print_test("Same-switch routing (h2 → h1)", True)
                return True
            else:
                self.print_test("Same-switch routing (h2 → h1)", False, 
                               f"Response: {result[:50]}")
                return False
                
        except Exception as e:
            self.print_test("Basic routing", False, str(e))
            return False
    
    def test_vip_request(self):
        """Test 6: Send request to VIP and verify it works"""
        self.print_header("TEST 6: VIP Request Handling")
        
        if not self.net:
            self.print_test("VIP request", False, "Network not started")
            return False
        
        try:
            h4 = self.net.get('h4')
            
            print("  Sending request to VIP (10.0.0.100)...")
            print("  This should be intercepted by controller...")
            
            # Send request to VIP
            result = h4.cmd('curl -s -m 5 http://10.0.0.100/ 2>&1')
            
            print(f"  Response received: {len(result)} bytes")
            print(f"  Content preview: {result[:100]}")
            
            # Check if we got a valid server response
            if 'Server:' in result or 'h1' in result or 'h2' in result or 'h3' in result:
                self.print_test("VIP request successful", True, 
                               "Got valid server response via VIP!")
                
                # Check which server responded
                for server in ['h1', 'h2', 'h3']:
                    if server in result:
                        print(f"       → Routed to {server}")
                        break
                
                return True
            else:
                self.print_test("VIP request successful", False, 
                               f"No valid response. Got: {result[:100]}")
                return False
                
        except Exception as e:
            self.print_test("VIP request", False, str(e))
            return False
    
    def test_vip_statistics(self):
        """Test 7: Check if VIP statistics are being tracked"""
        self.print_header("TEST 7: VIP Statistics Tracking")
        
        try:
            response = requests.get(f'{RYU_URL}/vip/stats', timeout=2)
            
            if response.status_code == 200:
                stats = response.json()
                
                total_requests = stats.get('total_requests', 0)
                server_selections = stats.get('server_selections', {})
                active_sessions = stats.get('active_sessions', 0)
                
                print(f"  Total VIP requests: {total_requests}")
                print(f"  Server selections: {server_selections}")
                print(f"  Active sessions: {active_sessions}")
                
                # Should have at least 1 request from previous test
                self.print_test("VIP statistics tracked", total_requests > 0,
                               f"{total_requests} requests recorded")
                
                # Check if server selection happened
                has_selections = len(server_selections) > 0
                self.print_test("Server selection recorded", has_selections,
                               f"Selections: {server_selections}")
                
                return total_requests > 0 and has_selections
            else:
                self.print_test("VIP statistics", False, f"Status: {response.status_code}")
                return False
                
        except Exception as e:
            self.print_test("VIP statistics", False, str(e))
            return False
    
    def test_multiple_vip_requests(self):
        """Test 8: Send multiple VIP requests and check load distribution"""
        self.print_header("TEST 8: Multiple VIP Requests")
        
        if not self.net:
            self.print_test("Multiple VIP requests", False, "Network not started")
            return False
        
        try:
            h4 = self.net.get('h4')
            
            print("  Sending 10 requests to VIP...")
            
            responses = []
            for i in range(10):
                result = h4.cmd('curl -s -m 3 http://10.0.0.100/')
                
                # Extract which server responded
                for server in ['h1', 'h2', 'h3']:
                    if server in result:
                        responses.append(server)
                        break
                
                time.sleep(0.5)
            
            print(f"  Responses received: {len(responses)}/10")
            
            # Count distribution
            from collections import Counter
            distribution = Counter(responses)
            
            print(f"  Server distribution: {dict(distribution)}")
            
            success_rate = len(responses) / 10.0
            self.print_test("Request success rate", success_rate >= 0.8,
                           f"{success_rate*100:.0f}% successful")
            
            # Check if multiple servers were used
            servers_used = len(distribution)
            self.print_test("Load distribution", servers_used >= 2,
                           f"{servers_used} different servers used")
            
            return success_rate >= 0.8
            
        except Exception as e:
            self.print_test("Multiple VIP requests", False, str(e))
            return False
    
    def test_flow_installation(self):
        """Test 9: Verify DNAT flows are installed"""
        self.print_header("TEST 9: DNAT Flow Installation")
        
        try:
            # Get flow stats for switch 200
            response = requests.get(f'{RYU_URL}/stats/flow/200', timeout=2)
            
            if response.status_code == 200:
                flows = response.json()
                
                print(f"  Total flows on switch 200: {len(flows)}")
                
                # Look for VIP-related flows
                vip_flows = 0
                for match_json, stats in flows.items():
                    if '10.0.0.100' in match_json:
                        vip_flows += 1
                        print(f"    Found VIP flow: {match_json[:80]}")
                
                self.print_test("DNAT flows installed", vip_flows > 0,
                               f"{vip_flows} VIP flows found")
                
                return vip_flows > 0
            else:
                self.print_test("Flow installation check", False, 
                               f"Status: {response.status_code}")
                return False
                
        except Exception as e:
            self.print_test("Flow installation", False, str(e))
            return False
    
    def cleanup(self):
        """Cleanup test environment"""
        self.print_header("CLEANUP")
        
        if self.net:
            print("Stopping HTTP servers...")
            for host_name in ['h1', 'h2', 'h3']:
                host = self.net.get(host_name)
                if host:
                    host.cmd('pkill -f "python3 -m http.server"')
            
            print("Stopping network...")
            self.net.stop()
        
        print("✅ Cleanup complete")
    
    def run_all_tests(self):
        """Run all tests in sequence"""
        print("\n" + "="*70)
        print("  VIP LOAD BALANCING CONTROLLER TEST SUITE")
        print("="*70)
        print("\nThis will test if your VIP controller is working correctly.")
        print("Tests will run in sequence, stopping if critical tests fail.\n")
        
        try:
            # Test 1: Controller running
            if not self.test_controller_running():
                print("\n❌ CRITICAL: Controller not running!")
                print("   Start controller: ryu-manager ryu_controller.py")
                return False
            
            # Test 2: VIP endpoint
            if not self.test_vip_endpoint():
                print("\n❌ CRITICAL: VIP endpoint not found!")
                print("   Make sure you're using ryu_controller_with_vip.py")
                return False
            
            # Test 3: Network setup
            if not self.test_network_setup():
                print("\n❌ CRITICAL: Network setup failed!")
                return False
            
            # Test 4: HTTP servers
            if not self.test_http_servers():
                print("\n⚠️  WARNING: Some HTTP servers not working")
                print("   Continuing with tests...")
            
            # Test 5: Basic routing
            if not self.test_basic_routing():
                print("\n⚠️  WARNING: Basic routing not working")
                print("   VIP might still work, continuing...")
            
            # Test 6: VIP request (CRITICAL)
            if not self.test_vip_request():
                print("\n❌ CRITICAL: VIP request failed!")
                print("   Check controller logs for errors")
                return False
            
            # Test 7: VIP statistics
            self.test_vip_statistics()
            
            # Test 8: Multiple requests
            self.test_multiple_vip_requests()
            
            # Test 9: Flow installation
            self.test_flow_installation()
            
            return True
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Tests interrupted by user")
            return False
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            return False
        finally:
            self.cleanup()
    
    def print_summary(self):
        """Print test summary"""
        self.print_header("TEST SUMMARY")
        
        total_tests = self.tests_passed + self.tests_failed
        success_rate = (self.tests_passed / total_tests * 100) if total_tests > 0 else 0
        
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {self.tests_passed} ✅")
        print(f"Failed: {self.tests_failed} ❌")
        print(f"Success Rate: {success_rate:.1f}%")
        
        if self.tests_failed == 0:
            print("\n🎉 ALL TESTS PASSED! 🎉")
            print("\nYour VIP controller is working correctly!")
            print("You can proceed to full training.")
        else:
            print("\n⚠️  SOME TESTS FAILED")
            print("\nFix the issues before proceeding to training.")
            print("Check controller logs for details.")
        
        print("="*70 + "\n")


def main():
    """Main entry point"""
    
    # Check if controller is specified
    print("\n" + "="*70)
    print("  VIP CONTROLLER TEST")
    print("="*70)
    print("\nPrerequisites:")
    print("  1. Ryu controller must be running:")
    print("     ryu-manager ryu_controller.py")
    print("  2. Controller must have VIP support (new version)")
    print("  3. Run with sudo (for Mininet)")
    print("\nStarting tests in 3 seconds...")
    print("="*70)
    
    time.sleep(3)
    
    # Run tests
    tester = VIPControllerTester()
    success = tester.run_all_tests()
    tester.print_summary()
    
    # Exit code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()