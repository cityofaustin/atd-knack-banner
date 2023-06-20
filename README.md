# Banner and Knack

This script synchronizes employee records between the enterprise Banner system and ATD's HR Knack application.

The script uses the employee ID field to identify new vs existing employees. If an employee ID in Banner does not exist in Knack, a new employee record will be created.

As well, if any of the following data in Banner is different from data in Knack, the Knack record will be updated to match Banner.

- Temporary vs full-time status
- Job title
- Email address
- Division name
- Employee name
- Position number
- Hire date

If an employee ID is present in Knack but not in Banner, the employee record will be marked as Inactive in Knack.

If an employee ID is present in Banner, but Inactive in Knack, the employee record will be set to Active in Knack.

And if an employee ID is present in Knack but marked as "Separated" in Knack, the script will not change the user's status.

## Get it running

Create a file called `env_file` with the variables defined in the `env_template` example.

Run the `update_employees.py` script, mounting your local copy of the repo into the container.

```
$ docker run -it --rm --env-file env_file -v ${PWD}:/app atddocker/atd-knack-banner:production ./update_employees.py
```

## Docker CI

Any merge to master or production will trigger a new docker image build/push.
