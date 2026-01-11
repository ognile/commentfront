    def post_facebook_comment_via_adb(
        self,
        adb_serial: str,
        post_url: str,
        comment: str
    ) -> bool:
        """
        NUCLEAR OPTION: Post comment using direct ADB commands.
        """
        import subprocess
        import time
        
        def adb_cmd(cmd):
            full_cmd = f"adb -s {adb_serial} shell {cmd}"
            logger.info(f"ADB: {cmd}")
            subprocess.run(full_cmd, shell=True, check=True)

        try:
            logger.info(f"Starting ADB automation on {adb_serial}")
            
            # 1. Force Open Facebook to the Post using native scheme
            # This bypasses the browser "Open With" dialog completely
            # Format: fb://facewebmodal/f?href={URL} is powerful
            deep_link = f"fb://facewebmodal/f?href={post_url}"
            adb_cmd(f'am start -a android.intent.action.VIEW -d "{deep_link}"')
            
            # Wait for load
            time.sleep(8)
            
            # 2. Tap "Comment" - Strategy: Use TAB navigation to be resolution independent?
            # Or just tap the bottom bar.
            # On most phones, the "Write a comment..." text box is at the very bottom.
            # We can try to tap the bottom center.
            # Assuming 1080x1920 or similar aspect ratio.
            # Safe bet: Tap 50% width, 95% height.
            
            # Get screen resolution first?
            # For now, let's just tap bottom center which usually focuses the input
            adb_cmd("input tap 540 2200") # High guess for modern screens
            adb_cmd("input tap 360 1200") # Fallback for smaller screens?
            
            # Better Strategy:
            # The "Write a comment" field is usually an EditText.
            # We can try to TAB into it.
            # Key 61 = TAB, Key 66 = ENTER
            # adb_cmd("input keyevent 61") 
            # adb_cmd("input keyevent 66") 
            
            # Let's try typing immediately. If input is focused, it works.
            time.sleep(2)
            
            # 3. Type Comment
            # 'input text' doesn't support spaces well, need to escape or use '%s'
            safe_comment = comment.replace(" ", "%s")
            adb_cmd(f"input text {safe_comment}")
            
            time.sleep(1)
            
            # 4. Send
            # Usually 'Enter' submits, or there is a send button.
            # Try Enter first
            adb_cmd("input keyevent 66")
            
            # Try tapping the "Send" arrow (usually right side of input)
            # adb_cmd("input tap 1000 2200")
            
            logger.info("ADB Automation sequence finished")
            return True
            
        except Exception as e:
            logger.error(f"ADB Automation failed: {e}")
            return False