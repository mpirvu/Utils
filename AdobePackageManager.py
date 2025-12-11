"""
AEM Adobe Package Manager Automation Script - DEBUG VERSION
Includes multiple debugging features and visual browser rendering
Created with Bob AI

Note: the server must already be up and running

## Prerequisites
1. **Python 3.7+** installed on your system
2. **Playwright** library (pip install playwright)
3. **Chromium browser library** (playwright install chromium)
"""

from playwright.sync_api import sync_playwright
import time

def automate_aem_package_workflow_debug():
    """
    Debug version with enhanced visibility and debugging features:
    - Runs in headed mode (visible browser)
    - Slower execution for observation
    - Screenshots at each step
    - Console log capture
    - Detailed error messages
    """

    # Configuration
    URL = "http://ub-cascadelake-2s32c-04.rtp.raleigh.ibm.com:9080/crx/packmgr/index.jsp"
    USERNAME = "admin"
    PASSWORD = "admin"
    PACKAGE_PATH = r"C:\Users\MariusPirvu\Downloads\sample_package_performance.zip"
    # Debug options
    ENABLE_SCREENSHOTS = False  # Set to False to disable all screenshots

    installation_time = 0

    with sync_playwright() as p:
        # Launch browser in HEADED mode with slow motion for debugging
        print("Launching browser in DEBUG mode...")
        browser = p.chromium.launch(
            headless=True,      # FALSE = visible browser window
            slow_mo=1000,        # 1 second delay between actions
            devtools=False        # Opens DevTools automatically (no)
        )

        # Create context with viewport size
        context = browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            #record_video_dir='./videos/'  # Records video of the session
        )

        page = context.new_page()

        # Enable console log capture
        page.on("console", lambda msg: print(f"[BROWSER CONSOLE] {msg.type}: {msg.text}"))

        # Enable request/response logging
        page.on("request", lambda request: print(f"[REQUEST] {request.method} {request.url}"))
        page.on("response", lambda response: print(f"[RESPONSE] {response.status} {response.url}"))

        try:
            print("\n" + "="*80)
            print("Step 1: Navigating to AEM Package Manager...")
            print("="*80)
            # Navigate and check for errors
            response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            status_code = response.status if response else None
            print(f"Response status: {status_code}")
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step1_initial_load.png')
                print("✓ Screenshot saved: debug_step1_initial_load.png")

            # Check if we got an error status code
            if status_code and status_code >= 400:
                print(f"⚠️  Received error status {status_code}, will retry with refresh...")
                needs_refresh = True
            else:
                print(f"✓ Page loaded successfully (status {status_code})")
                needs_refresh = False

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 2: Checking if refresh is needed...")
            print("="*80)

            if needs_refresh:
                print(f"Refreshing page due to previous error (status {status_code})...")
                time.sleep(5)
                #response = page.reload(wait_until='domcontentloaded', timeout=30000)
                response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
                new_status = response.status if response else None
                print(f"After refresh - Response status: {new_status}")

                if new_status and new_status >= 400:
                    print(f"✗ Still receiving error status {new_status} after refresh")
                    page.screenshot(path='debug_step2_error_after_refresh.png')
                    raise Exception(f"Page returned error status {new_status} even after refresh")
                else:
                    print(f"✓ Page loaded successfully after refresh (status {new_status})")
            else:
                print("✓ No refresh needed - page loaded successfully on first attempt")

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 3: Logging in with credentials...")
            print("="*80)
            # Check if login form exists
            if page.locator('input[name="j_username"]').count() > 0:
                print("Login form found!")
                page.fill('input[name="j_username"]', USERNAME)
                page.fill('input[name="j_password"]', PASSWORD)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step3_before_login.png')
                    print("✓ Screenshot saved: debug_step3_before_login.png")

                page.click('button[type="submit"]')
                page.wait_for_load_state('networkidle', timeout=30000)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step3_after_login.png')
                    print("✓ Screenshot saved: debug_step3_after_login.png")
            else:
                print("Already logged in or login form not found")
            time.sleep(2)

            print("\n" + "="*80)
            print("Step 4: Looking for 'Upload Package' button...")
            print("="*80)
            # Try multiple selectors for the upload button
            upload_selectors = [
                'button:has-text("Upload Package")',
                'button:text("Upload Package")',
                'a:has-text("Upload Package")',
                '[title="Upload Package"]'
            ]

            upload_button = None
            for selector in upload_selectors:
                if page.locator(selector).count() > 0:
                    print(f"✓ Found upload button with selector: {selector}")
                    upload_button = selector
                    break
            if upload_button:
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_before_upload_click.png')
                    print("✓ Screenshot saved: debug_step4_before_upload_click.png")
                page.click(upload_button)
                time.sleep(2)
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_after_upload_click.png')
                    print("✓ Screenshot saved: debug_step4_after_upload_click.png")
            else:
                print("✗ Upload button not found! Available buttons:")
                buttons = page.locator('button').all()
                for i, btn in enumerate(buttons[:10]):  # Show first 10 buttons
                    print(f"  Button {i}: {btn.text_content()}")
                if ENABLE_SCREENSHOTS:
                    page.screenshot(path='debug_step4_buttons_not_found.png')
                raise Exception("Upload Package button not found")

            print("\n" + "="*80)
            print("Step 5: Uploading package file...")
            print("="*80)
            page.set_input_files('input[type="file"]', PACKAGE_PATH)
            time.sleep(1)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step5_file_selected.png')
                print("✓ Screenshot saved: debug_step5_file_selected.png")

            print("\n" + "="*80)
            print("Step 6: Clicking OK to confirm upload...")
            print("="*80)
            page.click('button:has-text("OK")')
            page.wait_for_load_state('networkidle', timeout=60000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step6_upload_complete.png')
                print("✓ Screenshot saved: debug_step6_upload_complete.png")

            print("\n" + "="*80)
            print("Step 7: Clicking 'Install' button...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step7_before_install.png')
                print("✓ Screenshot saved: debug_step7_before_install.png")
            page.click('button:has-text("Install")')
            time.sleep(1)

            print("\n" + "="*80)
            print("Step 8: Confirming installation...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step8_install_dialog.png')
                print("✓ Screenshot saved: debug_step8_install_dialog.png")

            # Buttons are on the bottom border/footer of the dialog
            # Try multiple selectors including footer-specific ones
            install_selectors = [
                # Footer/bottom border selectors
                '.coral-Dialog-footer button:has-text("Install")',
                '.coral-Dialog-footer .coral-Button:has-text("Install")',
                'footer button:has-text("Install")',
                '.modal-footer button:has-text("Install")',
                'div[class*="footer"] button:has-text("Install")',
                'div[class*="Footer"] button:has-text("Install")',
                # Generic selectors
                'button:has-text("Install")',
                'button:text-is("Install")',
                'div[role="dialog"] button:has-text("Install")',
                '.coral-Dialog button:has-text("Install")',
                '.coral-Button:has-text("Install")',
                'button.coral-Button--primary:has-text("Install")',
                'button[type="button"]:has-text("Install")'
            ]

            print("Looking for Install button in dialog footer/border...")
            install_clicked = False
            for selector in install_selectors:
                try:
                    count = page.locator(selector).count()
                    if count > 0:
                        print(f"Found {count} button(s) with selector: {selector}")
                        # Wait for button to be visible and enabled
                        page.wait_for_selector(selector, state='visible', timeout=5000)

                        # Get the button's bounding box to see its position
                        button = page.locator(selector).first
                        box = button.bounding_box()
                        if box:
                            print(f"  Button position: x={box['x']}, y={box['y']}, width={box['width']}, height={box['height']}")

                        # Try clicking with force=True to bypass any overlays
                        button.click(force=True, timeout=5000)
                        print(f"✓ Clicked Install button with selector: {selector}")
                        install_clicked = True
                        break
                except Exception as e:
                    print(f"  Selector {selector} failed: {str(e)}")
                    continue

            if not install_clicked:
                print("\n✗ Could not find Install button with any selector!")
                print("\nDEBUG: Analyzing all buttons on page...")
                buttons = page.locator('button').all()
                print(f"Total buttons found: {len(buttons)}")
                for i, btn in enumerate(buttons):
                    try:
                        text = btn.text_content()
                        visible = btn.is_visible()
                        enabled = not btn.is_disabled()
                        box = btn.bounding_box()
                        print(f"\nButton {i}:")
                        print(f"  Text: '{text}'")
                        print(f"  Visible: {visible}")
                        print(f"  Enabled: {enabled}")
                        if box:
                            print(f"  Position: x={box['x']}, y={box['y']}")
                    except:
                        pass

                # Try clicking by coordinates if we can find the Install button
                print("\nAttempting to click by coordinates...")
                for btn in buttons:
                    try:
                        if btn.text_content() and "Install" in btn.text_content():
                            box = btn.bounding_box()
                            if box:
                                # Click in the center of the button
                                x = box['x'] + box['width'] / 2
                                y = box['y'] + box['height'] / 2
                                print(f"Clicking Install button at coordinates: ({x}, {y})")
                                page.mouse.click(x, y)
                                install_clicked = True
                                print("✓ Clicked Install button by coordinates")
                                break
                    except:
                        pass

                if not install_clicked:
                    raise Exception("Install button not found in dialog - check debug_step8_install_dialog.png")

            time.sleep(2)

            print("\n" + "="*80)
            print("Step 9: Waiting for installation to complete...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step9_install_dialog.png')
                print("✓ Screenshot saved: debug_step9_install_dialog.png")

            # It takes approximately 70 seconds for the window with progress bar indicator to disappear
            time.sleep(80)
            # The message appears in Activity Log frame at bottom - need to scroll it

            try:
                # Wait for the message
                message_locator = page.locator('text=/Package installed in \\d+ms/')
                message_locator.wait_for(timeout=120000)

                # Extract the installation time
                message_text = message_locator.first.text_content()
                print(f"✓ Found installation complete message: {message_text}")

                # Extract the time value using regex
                import re
                match = re.search(r'Package installed in (\d+)ms', message_text)
                if match:
                    installation_time = match.group(1)
                    print(f"\n{'='*80}")
                    print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
                    print(f"{'='*80}\n")

            except Exception as e:
                print(f"Could not find message with standard selector, trying alternatives...")
                # Try alternative patterns
                alt_patterns = [
                    'text=/installed in \\d+ms/',
                    'text=/Package installed/',
                    'text=/installed/',
                    '*:has-text("Package installed")',
                    '*:has-text("installed in")'
                ]

                message_found = False
                for pattern in alt_patterns:
                    try:
                        alt_locator = page.locator(pattern)
                        alt_locator.wait_for(timeout=10000)
                        message_text = alt_locator.first.text_content()
                        print(f"✓ Found message with pattern: {pattern}")
                        print(f"   Message: {message_text}")

                        # Try to extract time
                        import re
                        match = re.search(r'(\d+)\s*ms', message_text)
                        if match:
                            installation_time = match.group(1)
                            print(f"\n{'='*80}")
                            print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
                            print(f"{'='*80}\n")

                        message_found = True
                        break
                    except:
                        continue

                if not message_found:
                    print("✗ Could not find installation complete message")
                    if ENABLE_SCREENSHOTS:
                        print("Taking screenshot of current state...")
                        page.screenshot(path='debug_step9_message_not_found.png')
                    print("Check debug_step9_message_not_found.png")

                    # Print page text to see what's visible
                    print("\nSearching page text for 'installed'...")
                    page_text = page.content()
                    if 'installed' in page_text.lower():
                        print("✓ Found 'installed' in page content")
                    else:
                        print("✗ 'installed' not found in page content")


            print("\n" + "="*80)
            print("Step 10: Clicking 'More' button...")
            print("="*80)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step10_before_more.png')
                print("✓ Screenshot saved: debug_step10_before_more.png")
            page.click('button:has-text("More")')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step10_more_menu.png')
                print("✓ Screenshot saved: debug_step10_more_menu.png")

            print("\n" + "="*80)
            print("Step 11: Selecting 'Uninstall' option...")
            print("="*80)
            page.click('text="Uninstall"')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step11_uninstall_dialog.png')
                print("✓ Screenshot saved: debug_step11_uninstall_dialog.png")

            print("\n" + "="*80)
            print("Step 12: Confirming uninstall...")
            print("="*80)
            page.click('button:has-text("Uninstall")')
            page.wait_for_load_state('networkidle', timeout=60000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step12_uninstall_complete.png')
                print("✓ Screenshot saved: debug_step12_uninstall_complete.png")

            print("\n" + "="*80)
            print("Step 13: Clicking 'More' button again...")
            print("="*80)
            page.click('button:has-text("More")')
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step13_more_menu.png')
                print("✓ Screenshot saved: debug_step13_more_menu.png")

            print("\n" + "="*80)
            print("Step 14: Selecting 'Delete' option...")
            print("="*80)


            # Delete option appears at the top of the menu, separated by a line
            # Try multiple selectors to find it
            delete_selectors = [
                'text="Delete"',
                '*:has-text("Delete")',
                'a:has-text("Delete")',
                'button:has-text("Delete")',
                'li:has-text("Delete")',
                'div:has-text("Delete")',
                '[role="menuitem"]:has-text("Delete")',
                '.coral-Menu-item:has-text("Delete")',
                'coral-menu-item:has-text("Delete")'
            ]
            print("Looking for Delete option in menu (appears at top, separated by line)...")
            delete_clicked = False
            for selector in delete_selectors:
                try:
                    count = page.locator(selector).count()
                    if count > 0:
                        print(f"Found {count} element(s) with selector: {selector}")

                        # Get all matching elements
                        elements = page.locator(selector).all()
                        for i, elem in enumerate(elements):
                            try:
                                text = elem.text_content()
                                visible = elem.is_visible()
                                print(f"  Element {i}: text='{text}', visible={visible}")

                                # Click the visible Delete option
                                if visible and text and "Delete" in text:
                                    box = elem.bounding_box()
                                    if box:
                                        print(f"  Position: x={box['x']}, y={box['y']}")

                                    elem.click(force=True, timeout=5000)
                                    print(f"✓ Clicked Delete option with selector: {selector}")
                                    delete_clicked = True
                                    break
                            except Exception as e:
                                print(f"  Element {i} failed: {str(e)}")
                                continue

                        if delete_clicked:
                            break
                except Exception as e:
                    print(f"Selector {selector} failed: {str(e)}")
                    continue

            if not delete_clicked:
                print("\n✗ Could not find Delete option with any selector!")
                print("\nDEBUG: Analyzing all visible menu items...")

                # Try to find all menu items
                menu_selectors = ['li', '[role="menuitem"]', '.coral-Menu-item', 'a']
                for menu_sel in menu_selectors:
                    items = page.locator(menu_sel).all()
                    if len(items) > 0:
                        print(f"\nFound {len(items)} items with selector '{menu_sel}':")
                        for i, item in enumerate(items[:20]):  # Limit to first 20
                            try:
                                text = item.text_content()
                                visible = item.is_visible()
                                if visible and text:
                                    print(f"  Item {i}: '{text.strip()}'")
                                    if "Delete" in text:
                                        print(f"    ^ This is the Delete option! Clicking...")
                                        item.click(force=True)
                                        delete_clicked = True
                                        break
                            except:
                                pass
                        if delete_clicked:
                            break

                if not delete_clicked:
                    page.screenshot(path='debug_step14_delete_not_found.png')
                    raise Exception("Delete option not found in menu - check debug_step14_delete_not_found.png")

            time.sleep(1)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step14_delete_dialog.png')
                print("✓ Screenshot saved: debug_step14_delete_dialog.png")

            print("\n" + "="*80)
            print("Step 15: Confirming delete...")
            print("="*80)
            page.click('button:has-text("Delete")')
            page.wait_for_load_state('networkidle', timeout=30000)
            time.sleep(2)
            if ENABLE_SCREENSHOTS:
                page.screenshot(path='debug_step15_delete_complete.png')
                print("✓ Screenshot saved: debug_step15_delete_complete.png")

            print("\n" + "="*80)
            print("✓✓✓ ALL STEPS COMPLETED SUCCESSFULLY! ✓✓✓")
            print(f"⏱️  INSTALLATION TIME: {installation_time} ms")
            print("="*80)
            print("\nPackage has been uploaded, installed, uninstalled, and deleted.")
            if ENABLE_SCREENSHOTS:
                print("All screenshots saved in current directory.")
            #print("Video recording saved in ./videos/ directory.")

        except Exception as e:
            print("\n" + "="*80)
            print(f"✗✗✗ ERROR OCCURRED ✗✗✗")
            print("="*80)
            print(f"Error: {str(e)}")
            print(f"Error type: {type(e).__name__}")

            # Capture detailed error state
            page.screenshot(path='debug_error_screenshot.png')
            print("\n✓ Error screenshot saved: debug_error_screenshot.png")

            # Save page HTML for inspection
            with open('debug_error_page.html', 'w', encoding='utf-8') as f:
                f.write(page.content())
            print("✓ Page HTML saved: debug_error_page.html")

            # Print page title and URL
            print(f"\nPage Title: {page.title()}")
            print(f"Page URL: {page.url}")

            raise

        finally:
            # Keep browser open longer for inspection
            print("\nKeeping browser open for 10 seconds for inspection...")
            time.sleep(10)

            # Close context to save video
            context.close()
            browser.close()
            print("Browser closed. Check ./videos/ for session recording.")
    return installation_time


if __name__ == "__main__":
    print("=" * 80)
    print("AEM Package Manager Automation - DEBUG MODE")
    print("=" * 80)
    numIterations = 2
    resultsList = []
    for iter in range(numIterations):
        resultsList.append(automate_aem_package_workflow_debug())
    for iter in range(numIterations):
        print(f"Iter {iter} INSTALLATION TIME:", resultsList[iter], " ms")


# Made with Bob
