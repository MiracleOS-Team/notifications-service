import json
import time
import io
import os
import base64
import hashlib

from io import BytesIO
from datetime import datetime
from pathlib import Path

import dbus
import dbus.service
import dbus.mainloop.glib

from gi.repository import GLib
from PIL import Image





class NotificationDaemon(dbus.service.Object):
    def __init__(self, bus_name):
        super().__init__(bus_name, '/org/freedesktop/Notifications')
        self.id_counter = 1
        self.open_notifications = {}
        self.base_image_saving_path = "/tmp"
        try:
            self.open_notifications = json.load(open(os.path.join(os.getenv("HOME"), ".config/miracleos/notifications.json")))
            for notif in self.open_notifications:
                self.id_counter = max(self.id_counter, int(notif))
            self.id_counter += 1
        except:
            pass

        self._update_notification_count()

    def _save_image(self, base64_data):
        """
        Saves the image as a PNG to the specified base path if a file with the same hash does not already exist.

        :param base64_data: Base64-encoded string of the image.
        :return: Path to the saved image or existing file.
        """
        base_path = Path(self.base_image_saving_path).resolve()
        base_path.mkdir(parents=True, exist_ok=True)

        # Decode the base64 image and re-encode it as PNG
        image_bytes = base64.b64decode(base64_data)
        image = Image.open(BytesIO(image_bytes))
        png_buffer = BytesIO()
        image.save(png_buffer, format="PNG")
        png_data = png_buffer.getvalue()

        # Compute SHA256 hash of the re-encoded PNG
        image_hash = hashlib.sha256(png_data).hexdigest()

        # Check if an image with the same hash already exists
        image_path = base_path / f"{image_hash}.png"
        if image_path.exists():
            print(f"Duplicate image found: {image_path}")
            return image_path

        # Save the new PNG image
        with open(image_path, "wb") as image_file:
            image_file.write(png_data)
        print(f"Saved image: {image_path}")

        return image_path

    def _translate_notification_for_eww(self, notification_id):
        notification = self.open_notifications[notification_id]
        eww_str = "(notification "
        eww_str += f":app '{notification['app_name']}' "
        eww_str += f":summary '{notification['summary']}' "
        eww_str += f":body '{notification['body']}' " if notification['body'] is not None else ""
        eww_str += f":app_image '{notification['app_icon']}' " if notification['app_icon'] != "" else ":app_image '/usr/share/icons/MiracleOSIcons/16x16/mimetypes/application-x-executable.png' "
        eww_str += f":urgency {str(int(notification['hints']['urgency']))} "
        eww_str += f":desktop '{notification['hints']['desktop-entry']}' " if 'desktop-entry' in notification['hints'] else ""
        eww_str += f":id {notification_id} "

        if "image-data" in notification['hints']:
            img_path = self._save_image(notification['hints']['image-data'])

            eww_str += f":image '{img_path}' "
        elif "image-path" in notification['hints']:
            eww_str += f":image '{notification['hints']['image-path'].replace("file://", "")}' "
        elif "icon_data" in notification['hints']:
            img_path = self._save_image(notification['hints']['icon_data'])

            eww_str += f":image '{img_path}' "

        human_readable = datetime.fromtimestamp(notification['timestamp']).strftime("%H:%M")
        eww_str += f":time '{human_readable}' "

        return eww_str + ")"

    def _update_notification_count(self):
        # Save notifications to JSON and update count file
        json.dump(self.open_notifications, open(os.path.join(os.getenv("HOME"), ".config/miracleos/notifications.json"), "w"), indent=4)
        count_file_path = os.path.join(os.getenv("HOME"), ".config/miracleos/notification_count")
        os.makedirs(os.path.dirname(count_file_path), exist_ok=True)
        with open(count_file_path, "w+") as f:
            f.write(str(len(self.open_notifications)))

        # Update Eww Notifications
        eww_notifications = [self._translate_notification_for_eww(notif) for notif in self.open_notifications]
        with open(os.path.join(os.getenv("HOME"), ".config/miracleos/eww_notifications.json"), "w") as f:
            json.dump(eww_notifications, f)

    @dbus.service.method('org.freedesktop.Notifications', in_signature='', out_signature='as')
    def GetCapabilities(self):
        # Return supported capabilities (e.g., body text, actions, images)
        # TODO: Actions
        return ['body', "body-hyperlinks", 'icon-static', 'persistence', "actions"]

    @dbus.service.method('org.freedesktop.Notifications', in_signature='u', out_signature='')
    def CloseNotification(self, notification_id):
        print(f"Notification {notification_id} closed")

        if notification_id == 4294967295:
            for notif in list(self.open_notifications.keys()):
                del self.open_notifications[notif]
            self._update_notification_count()
            self.NotificationClosed(notification_id, 3)
            return

        if notification_id in self.open_notifications:
            del self.open_notifications[notification_id]
        self._update_notification_count()

        self.NotificationClosed(notification_id, 3)

    @dbus.service.signal('org.freedesktop.Notifications', signature='uu')
    def NotificationClosed(self, notification_id, reason):
        """Signal emitted when a notification is closed."""
        pass

    @dbus.service.method(
        'org.freedesktop.Notifications',
        in_signature='susssasa{sv}i',
        out_signature='u'
    )
    def Notify(self, app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout):
        # Handle notification replacement or new ID assignment
        notification_id = replaces_id if replaces_id else self.id_counter
        image_base64 = None

        # Decode image if provided in hints
        if "image-data" in hints:
            hints["image-data"] = self.decode_image_to_base64(hints["image-data"])
            print("Received image decoded to base64")

        if "icon_data" in hints:
            hints["icon_data"] = self.decode_image_to_base64(hints["icon_data"])
            print("Received icon decoded to base64")

        print("Notification received:")
        print(f"  App Name: {app_name}")
        print(f"  Summary: {summary}")
        print(f"  Body: {body}")
        print(f"  Icon: {app_icon}")
        print(f"  Actions: {actions}")
        print(f"  Hints: {hints}")
        print(f"  Timeout: {expire_timeout}")

        # Store the notification details
        self.open_notifications[notification_id] = {
            "app_name": app_name,
            "summary": summary,
            "body": body,
            "app_icon": app_icon.replace("file://", ""),
            "actions": actions,
            "hints": hints,
            "expire_timeout": expire_timeout,
            "timestamp": time.time()
        }
        self._update_notification_count()

        if not replaces_id:
            self.id_counter += 1

        return notification_id

    @dbus.service.method('org.freedesktop.Notifications', in_signature='', out_signature='ssss')
    def GetServerInformation(self):
        # Provide information about the notification daemon
        return ('MiracleOSNotificationDaemon', 'MiracleOS-Team', '1.0', '1.2')



    def decode_image_to_base64(self, image_data):
        """
        Decode a received image into a base64-encoded PNG.

        :param image_data: D-Bus struct of image data
        :return: Base64-encoded PNG string, or None if encoding fails
        """
        try:
            # Unpack the D-Bus struct
            width, height, rowstride, has_alpha, bits_per_sample, channels, raw_data = image_data

            # Validate image parameters
            if bits_per_sample != 8:
                raise ValueError("Unsupported bits per sample, only 8 bits are supported.")
            if channels not in (3, 4):
                raise ValueError("Unsupported number of channels, only 3 (RGB) or 4 (RGBA) are supported.")

            # Create an image mode based on the channel count
            mode = 'RGBA' if has_alpha and channels == 4 else 'RGB'

            # Create an Image object from the raw pixel data
            img = Image.frombytes(mode, (width, height), bytes(raw_data))

            # Save the image as a PNG into a bytes buffer
            with io.BytesIO() as buffer:
                img.save(buffer, format="PNG")
                base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

            return base64_image

        except Exception as e:
            print(f"Error encoding image to PNG: {e}")
            return None


def main():
    # Initialize the D-Bus main loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # Connect to the session bus
    try:
        bus = dbus.SessionBus()
    except Exception as e:
        print(f"Failed to connect to the D-Bus session bus: {e}")
        return

    # Request the name on the bus
    try:
        name = dbus.service.BusName('org.freedesktop.Notifications', bus)
    except Exception as e:
        print(f"Failed to request D-Bus name: {e}")
        return

    # Create the notification daemon
    daemon = NotificationDaemon(name)

    # Run the GLib event loop
    print("Notification daemon is running...")
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("Notification daemon stopped.")


if __name__ == '__main__':
    main()
