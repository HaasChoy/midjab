"""
Production-grade Naukri.com job scraper using Selenium with stealth configuration.

This module provides a robust, scalable scraper for Naukri.com job listings with
comprehensive error handling, anti-detection measures, and clean data extraction.
"""

import time
import logging
import random
import re
from typing import Optional, List, Set
from urllib.parse import urljoin, quote
from dataclasses import dataclass

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException, 
    WebDriverException,
    StaleElementReferenceException
)
from selenium_stealth import stealth
from webdriver_manager.chrome import ChromeDriverManager

from ..model import JobPost, Location, JobResponse, Scraper, ScraperInput, Site
from ..util import create_logger


@dataclass
class ScrapingMetrics:
    """Track scraping performance metrics."""
    pages_scraped: int = 0
    jobs_found: int = 0
    jobs_processed: int = 0
    jobs_skipped: int = 0
    errors_encountered: int = 0
    total_time: float = 0.0


class NaukriScrapingError(Exception):
    """Custom exception for Naukri scraping errors."""
    pass


class Naukri(Scraper):
    """
    Production-grade Selenium-based scraper for Naukri.com job listings.
    
    Features:
    - Stealth-configured Chrome WebDriver
    - Comprehensive error handling and recovery
    - Smart pagination with dynamic stopping
    - Rate limiting and respectful scraping
    - Detailed logging and metrics tracking
    - Robust data extraction and validation
    """
    
    # Class constants
    BASE_URL = "https://www.naukri.com"
    JOB_CONTAINER_CLASS = "cust-job-tuple"
    MAX_RETRIES = 3
    DEFAULT_DELAY = (2, 4)  # Random delay range in seconds
    PAGE_LOAD_TIMEOUT = 15
    ELEMENT_WAIT_TIMEOUT = 10
    
    # CSS selectors for job card elements
    SELECTORS = {
        'title': '.title a, .jobTuple-bdv h3 a, [data-job-title]',
        'company': '.subTitle a, .companyInfo a, [data-company-name]',
        'location': '.locationsContainer span, .location span, [data-job-location]',
        'experience': '.expwrap, .experience, [data-experience]',
        'salary': '.salary, .salaryInfo, [data-salary]',
        'job_url': '.title a, .jobTuple-bdv h3 a',
        'description': '.job-description, .jobDescription',
        'posted_date': '.jobTupleFooter .type, .postedDate'
    }
    
    def __init__(self, proxies: Optional[List[str]] = None, ca_cert: Optional[str] = None):
        """
        Initialize the Naukri scraper with enhanced configuration.
        
        Args:
            proxies: List of proxy servers (future enhancement)
            ca_cert: CA certificate path (future enhancement)
        """
        super().__init__(Site.NAUKRI)
        self.logger = create_logger("Naukri")
        
        # Configuration
        self.base_url = self.BASE_URL
        self.delay_range = self.DEFAULT_DELAY
        self.max_retries = self.MAX_RETRIES
        self.driver: Optional[webdriver.Chrome] = None
        self.metrics = ScrapingMetrics()
        
        # State tracking
        self.scraper_input: Optional[ScraperInput] = None
        self.seen_urls: Set[str] = set()
        
        self.logger.info("Naukri scraper initialized successfully")
    
    def _get_driver(self) -> webdriver.Chrome:
        """
        Create and configure a stealth-enabled Chrome WebDriver instance.
        
        Returns:
            webdriver.Chrome: Configured Chrome driver instance
            
        Raises:
            NaukriScrapingError: If driver initialization fails
        """
        self.logger.info("Configuring new Chrome browser instance with stealth settings")
        
        try:
            # Chrome options configuration
            options = webdriver.ChromeOptions()
            
            # User agent rotation for better stealth
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ]
            selected_user_agent = random.choice(user_agents)
            
            # Essential Chrome arguments for stealth and performance
            chrome_args = [
                f"--user-agent={selected_user_agent}",
                "--headless=new",  # Use new headless mode
                "--disable-gpu",
                "--window-size=1920,1200",
                "--ignore-certificate-errors",
                "--ignore-ssl-errors",
                "--ignore-certificate-errors-spki-list",
                "--disable-extensions",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--disable-features=VizDisplayCompositor"
            ]
            
            for arg in chrome_args:
                options.add_argument(arg)
            
            # Additional preferences for stealth
            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_settings.popups": 0,
                "profile.managed_default_content_settings.images": 2,  # Block images for faster loading
            }
            options.add_experimental_option("prefs", prefs)
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Initialize the driver with automatic ChromeDriver management
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            
            # Apply stealth patches
            stealth(
                driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
            
            # Set timeouts
            driver.set_page_load_timeout(self.PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(2)
            
            # Execute script to further mask automation
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            self.logger.info("Chrome driver configured successfully with stealth settings")
            return driver
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Chrome driver: {str(e)}")
            raise NaukriScrapingError(f"Driver initialization failed: {str(e)}")
    
    def _smart_delay(self):
        """Implement intelligent delay with randomization."""
        delay = random.uniform(*self.delay_range)
        self.logger.debug(f"Applying delay of {delay:.2f} seconds")
        time.sleep(delay)
    
    def _retry_on_failure(self, func, *args, max_retries: int = None, **kwargs):
        """
        Retry mechanism for operations that might fail due to network issues.
        
        Args:
            func: Function to retry
            *args: Function arguments
            max_retries: Maximum number of retries
            **kwargs: Function keyword arguments
            
        Returns:
            Function result or None if all retries failed
        """
        if max_retries is None:
            max_retries = self.max_retries
            
        last_exception = None
        
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    wait_time = (attempt + 1) * 2
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    self.metrics.errors_encountered += 1
                else:
                    self.logger.error(f"All {max_retries + 1} attempts failed. Last error: {str(e)}")
        
        return None
    
    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        """
        Main orchestrator method for scraping Naukri job listings.
        
        Args:
            scraper_input: Configuration for the scraping operation
            
        Returns:
            JobResponse: Container with scraped job data and metadata
        """
        start_time = time.time()
        self.scraper_input = scraper_input
        self.seen_urls.clear()
        self.metrics = ScrapingMetrics()
        
        job_posts: List[JobPost] = []
        
        try:
            self.logger.info(f"Starting Naukri scrape for '{scraper_input.search_term}' in '{scraper_input.location}'")
            
            # Initialize driver
            self.driver = self._get_driver()
            
            # Calculate maximum pages to scrape
            max_pages = min(10, (scraper_input.results_wanted // 20) + 2)  # ~20 jobs per page
            
            # Main pagination loop
            for page_num in range(1, max_pages + 1):
                self.logger.info(f"Scraping page {page_num} of {max_pages}")
                
                # Generate and navigate to URL
                url = self._generate_url(page_num)
                navigation_success = self._retry_on_failure(self._navigate_to_page, url)
                
                if not navigation_success:
                    self.logger.warning(f"Failed to navigate to page {page_num}, stopping pagination")
                    break
                
                # Extract job cards from the page
                job_cards = self._scrape_page()
                
                if not job_cards:
                    self.logger.info(f"No job cards found on page {page_num}, ending pagination")
                    break
                
                self.metrics.pages_scraped += 1
                self.metrics.jobs_found += len(job_cards)
                
                # Process each job card
                page_jobs_added = 0
                for job_card in job_cards:
                    if len(job_posts) >= scraper_input.results_wanted:
                        self.logger.info(f"Reached target of {scraper_input.results_wanted} jobs")
                        break
                    
                    job_post = self._process_job_card(job_card)
                    
                    if job_post and job_post.job_url and job_post.job_url not in self.seen_urls:
                        job_posts.append(job_post)
                        self.seen_urls.add(job_post.job_url)
                        page_jobs_added += 1
                        self.metrics.jobs_processed += 1
                    else:
                        self.metrics.jobs_skipped += 1
                
                self.logger.info(f"Page {page_num}: Added {page_jobs_added} new jobs")
                
                # Check if we have enough jobs
                if len(job_posts) >= scraper_input.results_wanted:
                    break
                
                # Respectful delay between pages
                self._smart_delay()
        
        except Exception as e:
            self.logger.error(f"Critical error during scraping: {str(e)}")
            raise NaukriScrapingError(f"Scraping failed: {str(e)}")
        
        finally:
            # Cleanup driver
            if self.driver:
                try:
                    self.logger.info("Closing browser instance")
                    self.driver.quit()
                    self.driver = None
                except Exception as e:
                    self.logger.warning(f"Error closing driver: {str(e)}")
        
        # Final metrics and cleanup
        self.metrics.total_time = time.time() - start_time
        final_jobs = job_posts[:scraper_input.results_wanted]
        
        self.logger.info(f"Scraping completed successfully:")
        self.logger.info(f"  - Pages scraped: {self.metrics.pages_scraped}")
        self.logger.info(f"  - Jobs found: {self.metrics.jobs_found}")
        self.logger.info(f"  - Jobs processed: {self.metrics.jobs_processed}")
        self.logger.info(f"  - Jobs returned: {len(final_jobs)}")
        self.logger.info(f"  - Total time: {self.metrics.total_time:.2f}s")
        
        return JobResponse(job_posts=final_jobs)
    
    def _navigate_to_page(self, url: str) -> bool:
        """
        Navigate to a specific page with error handling.
        
        Args:
            url: Target URL to navigate to
            
        Returns:
            bool: True if navigation successful, False otherwise
        """
        try:
            self.driver.get(url)
            self.logger.debug(f"Successfully navigated to: {url}")
            
            # Wait for page to stabilize
            time.sleep(2)
            return True
            
        except TimeoutException:
            self.logger.warning(f"Page load timeout for URL: {url}")
            return False
        except WebDriverException as e:
            self.logger.warning(f"WebDriver error navigating to {url}: {str(e)}")
            return False
    
    def _scrape_page(self) -> List:
        """
        Extract job cards from the current page with smart waiting.
        
        Returns:
            List: List of Selenium WebElements representing job cards
        """
        try:
            # Wait for job containers to load
            wait = WebDriverWait(self.driver, self.ELEMENT_WAIT_TIMEOUT)
            
            # Multiple selectors for job containers (Naukri updates their HTML frequently)
            selectors_to_try = [
                f".{self.JOB_CONTAINER_CLASS}",
                ".jobTuple",
                ".job-tuple",
                "[data-job-id]",
                ".srp-jobtuple-wrapper"
            ]
            
            job_cards = []
            for selector in selectors_to_try:
                try:
                    job_cards = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector)))
                    if job_cards:
                        self.logger.debug(f"Found {len(job_cards)} job cards using selector: {selector}")
                        break
                except TimeoutException:
                    continue
            
            if not job_cards:
                self.logger.warning("No job cards found with any selector")
                return []
            
            return job_cards
            
        except TimeoutException:
            self.logger.warning("Timeout waiting for job cards to load")
            return []
        except Exception as e:
            self.logger.error(f"Error scraping page: {str(e)}")
            return []
    
    def _process_job_card(self, job_card) -> Optional[JobPost]:
        """
        Extract job information from a single job card element.
        
        Args:
            job_card: Selenium WebElement representing a job card
            
        Returns:
            JobPost: Parsed job information or None if parsing fails
        """
        try:
            # Helper function to safely extract text from elements
            def safe_extract(selectors: str, attribute: str = 'text') -> str:
                for selector in selectors.split(','):
                    selector = selector.strip()
                    try:
                        element = job_card.find_element(By.CSS_SELECTOR, selector)
                        if attribute == 'text':
                            return element.text.strip()
                        elif attribute == 'href':
                            return element.get_attribute('href') or ''
                    except (NoSuchElementException, StaleElementReferenceException):
                        continue
                return ''
            
            # Extract job details
            title = safe_extract(self.SELECTORS['title'])
            company = safe_extract(self.SELECTORS['company'])
            location_text = safe_extract(self.SELECTORS['location'])
            job_url = safe_extract(self.SELECTORS['job_url'], 'href')
            experience = safe_extract(self.SELECTORS['experience'])
            salary = safe_extract(self.SELECTORS['salary'])
            
            # Validate required fields
            if not title or not company:
                self.logger.debug("Skipping job card: missing title or company")
                return None
            
            # Clean and process the extracted data
            title = self._clean_text(title)
            company = self._clean_text(company)
            location_text = self._clean_text(location_text)
            
            # Process location
            location = self._parse_location(location_text) if location_text else None
            
            # Make job URL absolute
            if job_url and not job_url.startswith('http'):
                job_url = urljoin(self.base_url, job_url)
            
            # Process salary and experience
            salary_min, salary_max = self._parse_salary(salary)
            experience_min, experience_max = self._parse_experience(experience)
            
            # Create JobPost object
            job_post = JobPost(
                title=title,
                company=company,
                location=location,
                job_url=job_url,
                date_posted=None,  # Naukri doesn't always show posting dates clearly
                salary_min=salary_min,
                salary_max=salary_max,
                description=None,  # Would require additional page load
                company_url=None,
                emails=None,
                is_remote=self._is_remote_job(title, location_text),
                job_type=None,
                job_function=None,
                experience_min=experience_min,
                experience_max=experience_max
            )
            
            return job_post
            
        except Exception as e:
            self.logger.warning(f"Failed to process job card: {str(e)}")
            return None
    
    def _generate_url(self, page_num: int) -> str:
        """
        Generate the URL for a specific page of search results.
        
        Args:
            page_num: Page number to generate URL for
            
        Returns:
            str: Complete URL for the specified page
        """
        search_term = self.scraper_input.search_term.lower().replace(' ', '-')
        location = self.scraper_input.location.lower().replace(' ', '-')
        
        # URL encode special characters
        search_term = quote(search_term, safe='-')
        location = quote(location, safe='-')
        
        # Base URL construction
        if location.lower() in ['india', 'all']:
            base_url = f"{self.base_url}/{search_term}-jobs"
        else:
            base_url = f"{self.base_url}/{search_term}-jobs-in-{location}"
        
        # Add page number for pages beyond the first
        if page_num > 1:
            url = f"{base_url}-{page_num}"
        else:
            url = base_url
        
        self.logger.debug(f"Generated URL for page {page_num}: {url}")
        return url
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize text data."""
        if not text:
            return ""
        
        # Remove extra whitespace and normalize
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Remove common artifacts
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'[\r\t]+', ' ', text)
        
        return text.strip()
    
    def _parse_location(self, location_text: str) -> Optional[Location]:
        """Parse location string into Location object."""
        if not location_text:
            return None
        
        # Simple location parsing - can be enhanced
        location_parts = location_text.split(',')
        city = location_parts[0].strip() if location_parts else location_text
        
        return Location(
            city=city,
            state=None,
            country="India"  # Naukri is primarily for Indian jobs
        )
    
    def _parse_salary(self, salary_text: str) -> tuple[Optional[int], Optional[int]]:
        """Parse salary string and extract min/max values."""
        if not salary_text:
            return None, None
        
        # Look for salary patterns (lakhs, thousands)
        salary_pattern = r'(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(lakh|thousand|k|l)'
        match = re.search(salary_pattern, salary_text.lower())
        
        if match:
            min_sal, max_sal, unit = match.groups()
            multiplier = 100000 if unit in ['lakh', 'l'] else 1000
            
            try:
                return int(float(min_sal) * multiplier), int(float(max_sal) * multiplier)
            except ValueError:
                pass
        
        return None, None
    
    def _parse_experience(self, experience_text: str) -> tuple[Optional[int], Optional[int]]:
        """Parse experience string and extract min/max years."""
        if not experience_text:
            return None, None
        
        # Look for experience patterns
        exp_pattern = r'(\d+)\s*-\s*(\d+)\s*year'
        match = re.search(exp_pattern, experience_text.lower())
        
        if match:
            try:
                return int(match.group(1)), int(match.group(2))
            except ValueError:
                pass
        
        return None, None
    
    def _is_remote_job(self, title: str, location: str) -> bool:
        """Determine if a job is remote based on title and location."""
        remote_keywords = ['remote', 'work from home', 'wfh', 'telecommute', 'virtual']
        
        text_to_check = f"{title} {location}".lower()
        return any(keyword in text_to_check for keyword in remote_keywords)