#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

import httpx
import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JudgeAgent:
    """Production-grade Judge Agent for processing activity streams."""
    
    def __init__(self):
        """Initialize the Judge Agent with configuration from environment variables."""
        # RabbitMQ Configuration
        self.rabbitmq_host = os.getenv('RABBITMQ_HOST', 'rabbitmq')
        self.consume_queue = os.getenv('CONSUME_QUEUE', 'activity_stream')
        self.publish_queue = os.getenv('PUBLISH_QUEUE', 'judged_activity_stream')
        self.dead_letter_queue = os.getenv('DEAD_LETTER_QUEUE', 'activity_stream_dlq')
        
        # API Configuration
        self.planner_api_url = os.getenv('PLANNER_API_URL', 'http://planner:8000/api/goals/active')
        self.ollama_api_url = os.getenv('OLLAMA_API_URL', 'http://ollama:11434/api/generate')
        
        # State management
        self.connection = None
        self.channel = None
        self.http_client = None
        self.current_goal = None
        self.goal_last_updated = None
        self.verdict_cache = {}  # Simple in-memory cache
        self.cache_ttl = timedelta(hours=1)  # Cache verdicts for 1 hour
        
        # Configuration
        self.goal_refresh_interval = 300  # 5 minutes
        self.llm_timeout = 30  # 30 seconds for LLM requests
        self.max_retries = 3
        
        # Thread control
        self._stop_event = threading.Event()
        self._goal_refresh_thread = None
        
    async def _refresh_goal(self) -> bool:
        """
        Fetch the current goal from the Planner API.
        
        Returns:
            bool: True if goal was successfully refreshed, False otherwise
        """
        try:
            logger.info("Refreshing current goal from Planner API")
            response = await self.http_client.get(
                self.planner_api_url,
                timeout=10.0
            )
            response.raise_for_status()
            
            goal_data = response.json()
            if goal_data and 'goal' in goal_data:
                self.current_goal = goal_data['goal']
                self.goal_last_updated = datetime.now()
                logger.info(f"Goal refreshed successfully: {self.current_goal}")
                return True
            else:
                logger.warning("No active goal found in Planner API response")
                return False
                
        except httpx.TimeoutException:
            logger.error("Timeout while fetching goal from Planner API")
            return False
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while fetching goal: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error while fetching goal: {str(e)}")
            return False
    
    def _goal_refresh_worker(self):
        """Background worker to periodically refresh the goal."""
        while not self._stop_event.wait(self.goal_refresh_interval):
            try:
                # Run the async goal refresh in the event loop
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._refresh_goal())
                loop.close()
            except Exception as e:
                logger.error(f"Error in goal refresh worker: {str(e)}")
    
    def _get_cache_key(self, activity_data: Dict[str, Any]) -> str:
        """Generate a cache key for the activity data."""
        # Create a simple hash based on activity content
        activity_str = json.dumps(activity_data, sort_keys=True)
        return f"verdict_{hash(activity_str)}"
    
    def _is_cache_valid(self, timestamp: datetime) -> bool:
        """Check if a cached verdict is still valid."""
        return datetime.now() - timestamp < self.cache_ttl
    
    async def _get_llm_verdict(self, activity_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Get verdict from the LLM via Ollama API.
        
        Args:
            activity_data: The activity data to judge
            
        Returns:
            Dict with verdict data or None if failed
        """
        if not self.current_goal:
            logger.error("No current goal available for judgment")
            return None
        
        # Check cache first
        cache_key = self._get_cache_key(activity_data)
        if cache_key in self.verdict_cache:
            cached_verdict, timestamp = self.verdict_cache[cache_key]
            if self._is_cache_valid(timestamp):
                logger.info("Using cached verdict")
                return cached_verdict
            else:
                # Remove expired cache entry
                del self.verdict_cache[cache_key]
        
        # Construct the prompt
        prompt = f"""You are an AI judge evaluating user activities against their stated goal.

CURRENT USER GOAL: {self.current_goal}

ACTIVITY TO JUDGE: {json.dumps(activity_data, indent=2)}

Please evaluate this activity and respond with a JSON object containing:
- "verdict": "SUPPORTS_GOAL", "NEUTRAL", or "HINDERS_GOAL"
- "confidence_score": float between 0.0 and 1.0
- "category": brief category of the activity (e.g., "productivity", "entertainment", "exercise")
- "reason": brief explanation of your judgment

Respond only with valid JSON, no other text."""

        request_payload = {
            "model": "llama3.2:3b",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "top_p": 0.9,
                "num_predict": 200
            }
        }
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Getting LLM verdict (attempt {attempt + 1}/{self.max_retries})")
                response = await self.http_client.post(
                    self.ollama_api_url,
                    json=request_payload,
                    timeout=self.llm_timeout
                )
                response.raise_for_status()
                
                ollama_response = response.json()
                if 'response' not in ollama_response:
                    logger.error("Invalid response format from Ollama")
                    continue
                
                # Parse the LLM's JSON response
                llm_text = ollama_response['response'].strip()
                
                # Try to extract JSON from the response
                try:
                    # Find JSON in the response (in case there's extra text)
                    start_idx = llm_text.find('{')
                    end_idx = llm_text.rfind('}') + 1
                    if start_idx != -1 and end_idx != 0:
                        json_str = llm_text[start_idx:end_idx]
                        verdict_data = json.loads(json_str)
                        
                        # Validate required fields
                        required_fields = ['verdict', 'confidence_score', 'category', 'reason']
                        if all(field in verdict_data for field in required_fields):
                            # Cache the verdict
                            self.verdict_cache[cache_key] = (verdict_data, datetime.now())
                            logger.info(f"LLM verdict obtained: {verdict_data['verdict']} (confidence: {verdict_data['confidence_score']})")
                            return verdict_data
                        else:
                            logger.error(f"Missing required fields in LLM response: {verdict_data}")
                    else:
                        logger.error(f"No valid JSON found in LLM response: {llm_text}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse LLM JSON response: {e}")
                    logger.error(f"LLM response was: {llm_text}")
                
            except httpx.TimeoutException:
                logger.error(f"Timeout on LLM request (attempt {attempt + 1})")
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error from Ollama API: {e.response.status_code}")
            except Exception as e:
                logger.error(f"Unexpected error during LLM request: {str(e)}")
            
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error("Failed to get LLM verdict after all retries")
        return None
    
    async def _process_message_async(self, ch, method, properties, body):
        """
        Async helper function to process the message.
        
        Args:
            ch: Channel
            method: Delivery method
            properties: Message properties
            body: Message body
        """
        try:
            # Parse the incoming message
            try:
                message_data = json.loads(body.decode('utf-8'))
                logger.info(f"Processing message: {message_data.get('id', 'unknown')}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message JSON: {e}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            
            # Get LLM verdict
            verdict_data = await self._get_llm_verdict(message_data)
            if not verdict_data:
                logger.error("Failed to get LLM verdict, sending to DLQ")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            
            # Create enriched message
            enriched_message = {
                **message_data,  # Original data
                'judgment': {
                    'verdict': verdict_data['verdict'],
                    'confidence_score': verdict_data['confidence_score'],
                    'category': verdict_data['category'],
                    'reason': verdict_data['reason'],
                    'judged_at': datetime.now().isoformat(),
                    'goal_at_judgment': self.current_goal
                }
            }
            
            # Publish enriched message
            ch.basic_publish(
                exchange='',
                routing_key=self.publish_queue,
                body=json.dumps(enriched_message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type='application/json'
                )
            )
            
            logger.info(f"Published enriched message with verdict: {verdict_data['verdict']}")
            
            # Acknowledge the original message
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    
    def on_message(self, ch, method, properties, body):
        """
        Callback for processing RabbitMQ messages.
        Bridges sync pika callback to async processing logic.
        """
        try:
            # Run the async processing in a new event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                self._process_message_async(ch, method, properties, body)
            )
            loop.close()
        except Exception as e:
            logger.error(f"Error in message callback: {str(e)}")
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                logger.error("Failed to nack message after error")
    
    def _setup_rabbitmq(self):
        """Set up RabbitMQ connection, channel, and queues."""
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                logger.info(f"Connecting to RabbitMQ (attempt {attempt + 1}/{max_attempts})")
                
                # Establish connection
                connection_params = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
                self.connection = pika.BlockingConnection(connection_params)
                self.channel = self.connection.channel()
                
                # Declare Dead Letter Queue first
                self.channel.queue_declare(
                    queue=self.dead_letter_queue,
                    durable=True
                )
                
                # Declare consume queue with DLQ configuration
                self.channel.queue_declare(
                    queue=self.consume_queue,
                    durable=True,
                    arguments={
                        'x-dead-letter-exchange': '',  # Use default exchange
                        'x-dead-letter-routing-key': self.dead_letter_queue
                    }
                )
                
                # Declare publish queue
                self.channel.queue_declare(
                    queue=self.publish_queue,
                    durable=True
                )
                
                # Set QoS to process one message at a time
                self.channel.basic_qos(prefetch_count=1)
                
                logger.info("RabbitMQ setup completed successfully")
                return
                
            except AMQPConnectionError as e:
                logger.error(f"Failed to connect to RabbitMQ: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise
            except Exception as e:
                logger.error(f"Unexpected error setting up RabbitMQ: {e}")
                raise
    
    async def _initialize_async_components(self):
        """Initialize async components like HTTP client and goal fetching."""
        # Initialize HTTP client
        self.http_client = httpx.AsyncClient()
        
        # Fetch initial goal
        success = await self._refresh_goal()
        if not success:
            logger.warning("Failed to fetch initial goal, will retry periodically")
    
    def run(self):
        """Main method to run the Judge Agent."""
        try:
            logger.info("Starting Judge Agent")
            
            # Initialize async components
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._initialize_async_components())
            loop.close()
            
            # Set up RabbitMQ
            self._setup_rabbitmq()
            
            # Start goal refresh background thread
            self._goal_refresh_thread = threading.Thread(
                target=self._goal_refresh_worker,
                daemon=True
            )
            self._goal_refresh_thread.start()
            logger.info("Started goal refresh background thread")
            
            # Set up consumer
            self.channel.basic_consume(
                queue=self.consume_queue,
                on_message_callback=self.on_message
            )
            
            logger.info(f"Judge Agent started. Consuming from '{self.consume_queue}', publishing to '{self.publish_queue}'")
            logger.info("Waiting for messages. To exit press CTRL+C")
            
            # Start consuming
            self.channel.start_consuming()
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
            self._shutdown()
        except Exception as e:
            logger.error(f"Fatal error in Judge Agent: {str(e)}")
            sys.exit(1)
    
    def _shutdown(self):
        """Clean shutdown of the Judge Agent."""
        logger.info("Shutting down Judge Agent...")
        
        # Stop background threads
        self._stop_event.set()
        if self._goal_refresh_thread and self._goal_refresh_thread.is_alive():
            self._goal_refresh_thread.join(timeout=5)
        
        # Close RabbitMQ connections
        if self.channel and not self.channel.is_closed:
            try:
                self.channel.stop_consuming()
                self.channel.close()
            except Exception as e:
                logger.error(f"Error closing channel: {e}")
        
        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
        
        # Close HTTP client
        if self.http_client:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.http_client.aclose())
                loop.close()
            except Exception as e:
                logger.error(f"Error closing HTTP client: {e}")
        
        logger.info("Judge Agent shutdown complete")


def main():
    """Entry point for the Judge Agent."""
    agent = JudgeAgent()
    agent.run()


if __name__ == "__main__":
    main()