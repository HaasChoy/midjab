#!/usr/bin/env python3

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

import pika
import psutil
from Xlib import display
from pynput import keyboard, mouse


class ObserverAgent:
    def __init__(self):
        self.config = self._load_config()
        self._setup_logging()
        
        self.last_activity = time.time()
        self.last_process_name = None
        self.last_window_title = None
        self.last_user_status = None
        self.message_buffer: List[Dict[str, Any]] = []
        
        self.connection = None
        self.channel = None
        self.retry_delay = 1
        self.max_retry_delay = 60
        
        self._start_activity_listeners()

    def _load_config(self) -> Dict[str, Any]:
        return {
            'rabbitmq_host': os.getenv('RABBITMQ_HOST', 'localhost'),
            'queue_name': os.getenv('QUEUE_NAME', 'observer_queue'),
            'idle_timeout': int(os.getenv('IDLE_TIMEOUT_SECONDS', '300')),
            'check_interval': int(os.getenv('CHECK_INTERVAL_SECONDS', '2'))
        }

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)

    def _on_activity(self, *args):
        self.last_activity = time.time()

    def _start_activity_listeners(self):
        keyboard_listener = keyboard.Listener(
            on_press=self._on_activity,
            on_release=self._on_activity
        )
        mouse_listener = mouse.Listener(
            on_move=self._on_activity,
            on_click=self._on_activity,
            on_scroll=self._on_activity
        )
        
        keyboard_listener.daemon = True
        mouse_listener.daemon = True
        
        keyboard_listener.start()
        mouse_listener.start()

    def _connect_rabbitmq(self) -> bool:
        try:
            if self.connection and not self.connection.is_closed:
                return True

            connection_params = pika.ConnectionParameters(
                host=self.config['rabbitmq_host'],
                heartbeat=600,
                blocked_connection_timeout=300
            )
            
            self.connection = pika.BlockingConnection(connection_params)
            self.channel = self.connection.channel()
            self.channel.queue_declare(queue=self.config['queue_name'], durable=True)
            
            self.retry_delay = 1
            self.logger.info("RabbitMQ connection established")
            return True
            
        except Exception as e:
            self.logger.error(f"RabbitMQ connection failed: {e}")
            self._exponential_backoff()
            return False

    def _exponential_backoff(self):
        time.sleep(self.retry_delay)
        self.retry_delay = min(self.retry_delay * 2, self.max_retry_delay)

    def _publish_message(self, message: Dict[str, Any]) -> bool:
        if not self._connect_rabbitmq():
            return False

        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=self.config['queue_name'],
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    timestamp=int(time.time())
                )
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Message publish failed: {e}")
            self._close_connection()
            return False

    def _close_connection(self):
        try:
            if self.channel:
                self.channel.close()
            if self.connection and not self.connection.is_closed:
                self.connection.close()
        except:
            pass
        finally:
            self.connection = None
            self.channel = None

    def _get_user_status(self) -> str:
        time_since_activity = time.time() - self.last_activity
        return "idle" if time_since_activity > self.config['idle_timeout'] else "active"

    def _get_current_state(self) -> tuple:
        window_title = "No Active Window"
        process_name = "Unknown"
        
        try:
            disp = display.Display()
            root = disp.screen().root
            
            net_active_window = disp.intern_atom('_NET_ACTIVE_WINDOW')
            active_window_id = root.get_full_property(net_active_window, 0)
            
            disp.sync()
            
            if active_window_id and active_window_id.value and len(active_window_id.value) > 0:
                try:
                    active_window = disp.create_resource_object('window', active_window_id.value[0])
                    
                    window_title = active_window.get_wm_name()
                    if not window_title:
                        window_title = active_window.get_wm_class()
                        if window_title and len(window_title) > 0:
                            window_title = window_title[1] if len(window_title) > 1 else window_title[0]
                        else:
                            window_title = "Unknown Window"
                    
                    wm_pid = disp.intern_atom('_NET_WM_PID')
                    pid_property = active_window.get_full_property(wm_pid, 0)
                    
                    if pid_property and pid_property.value and len(pid_property.value) > 0:
                        pid = pid_property.value[0]
                        if pid > 0:
                            try:
                                process = psutil.Process(pid)
                                process_name = process.name()
                            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                                process_name = "Process Not Found"
                        else:
                            process_name = "Invalid PID"
                    else:
                        process_name = "No PID Available"
                        
                except Exception as window_error:
                    self.logger.debug(f"Error accessing window properties: {window_error}")
                    window_title = "Window Access Error"
                    process_name = "Window Access Error"
            
            disp.close()
                
        except Exception as e:
            self.logger.debug(f"Error connecting to X display: {e}")
            window_title = "Display Error"
            process_name = "Display Error"

        user_status = self._get_user_status()
        return process_name, window_title, user_status

    def _create_message(self, process_name: str, window_title: str, user_status: str) -> Dict[str, Any]:
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'process_name': process_name,
            'window_title': window_title,
            'user_status': user_status
        }

    def _flush_buffer(self):
        if not self.message_buffer:
            return

        successful_messages = []
        for message in self.message_buffer:
            if self._publish_message(message):
                successful_messages.append(message)
            else:
                break

        for msg in successful_messages:
            self.message_buffer.remove(msg)

        if successful_messages:
            self.logger.info(f"Flushed {len(successful_messages)} buffered messages")

    def _handle_state_change(self, process_name: str, window_title: str, user_status: str):
        message = self._create_message(process_name, window_title, user_status)
        
        if self._publish_message(message):
            self._flush_buffer()
            self.logger.info(f"Published: {process_name} | {user_status}")
        else:
            self.message_buffer.append(message)
            self.logger.warning(f"Buffered message (total: {len(self.message_buffer)})")

        self.last_process_name = process_name
        self.last_window_title = window_title
        self.last_user_status = user_status

    def run(self):
        self.logger.info("Observer Agent starting...")
        self.logger.info(f"Config: {self.config}")
        
        while True:
            try:
                process_name, window_title, user_status = self._get_current_state()
                
                state_changed = (
                    process_name != self.last_process_name or
                    window_title != self.last_window_title or
                    user_status != self.last_user_status
                )
                
                if state_changed:
                    self._handle_state_change(process_name, window_title, user_status)
                
                time.sleep(self.config['check_interval'])
                
            except KeyboardInterrupt:
                self.logger.info("Shutting down Observer Agent...")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                time.sleep(5)
        
        self._close_connection()
        self.logger.info("Observer Agent stopped")


if __name__ == "__main__":
    agent = ObserverAgent()
    agent.run()