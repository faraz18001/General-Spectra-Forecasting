"""
Special events representation for forecasting holiday and custom calendar events.

This module provides structures to format special events like national holidays, 
Ramadan, Eid, or custom calendar events into formats that the Prophet model 
can ingest as holiday regressors.
"""

import pandas as pd


class Event:
    """
    Represents a custom calendar event or holiday with a date window.

    Attributes:
        holiday (str): The unique name of the holiday/event.
        start_date (str): The start date of the event in 'YYYY-MM-DD' format.
        end_date (str): The end date of the event in 'YYYY-MM-DD' format.
        lower_window (int): Number of days before the start_date to extend the event impact.
        upper_window (int): Number of days after the end_date to extend the event impact.
    """

    def __init__(
        self,
        holiday: str,
        start_date: str,
        end_date: str,
        lower_window=0,
        upper_window=0,
    ):
        """
        Initializes an Event instance.

        Args:
            holiday (str): The unique name of the holiday/event (e.g., 'Eid Al Fitr').
            start_date (str): Start date in 'YYYY-MM-DD' format (e.g., '2026-03-20').
            end_date (str): End date in 'YYYY-MM-DD' format (e.g., '2026-03-22').
            lower_window (int, optional): Days before to include. Defaults to 0.
            upper_window (int, optional): Days after to include. Defaults to 0.
        """
        self.holiday = holiday
        self.start_date = start_date
        self.end_date = end_date
        self.lower_window = lower_window
        self.upper_window = upper_window

    def create_event(self):
        """
        Generates a pandas DataFrame representation of the event days.

        Expands the start and end dates into a daily range, assigning the event name 
        and lower/upper window offsets to each row.

        Args:
            None

        Returns:
            pandas.DataFrame: A DataFrame with the event day timeline, formatted for the Prophet model.
                
                DataFrame Columns and Types:
                    - holiday (object/str): Name of the event.
                    - ds (datetime64[ns]): Exact date of each day of the event.
                    - lower_window (int64): The lower offset window.
                    - upper_window (int64): The upper offset window.

        Example Output:
            Assuming an Event named 'National Day' from '2026-12-01' to '2026-12-02':
            
            ```python
               holiday         ds  lower_window  upper_window
            0  National Day 2026-12-01             0             0
            1  National Day 2026-12-02             0             0
            ```
        """
        created_event = pd.DataFrame(
            {
                "holiday": self.holiday,
                "ds": pd.date_range(self.start_date, self.end_date),
                "lower_window": self.lower_window,
                "upper_window": self.upper_window,
            }
        )
        return created_event

