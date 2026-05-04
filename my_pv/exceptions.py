"""
   Copyright 2026 my-PV GmbH, Austria

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

This file defines the different exceptions and errors the my-PV library can raise.
"""


class MyPVException(Exception):
    """
    Generic my-PV exception.
    """


class MyPVConnectionError(MyPVException):
    """
    my-PV connection error.
    """


class MyPVAuthenticationError(MyPVException):
    """
    my-PV authentication error.
    """


class MyPVNotSupportedError(MyPVException):
    """
    My-PV functionality not supported error.
    """
