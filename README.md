# Vibin Server

## Installation

### Raspberry Pi 4

Install [Ubuntu (22.10)](https://ubuntu.com/download/raspberry-pi).

#### Ubuntu configuration

##### Prepare an application directory

```bash
cd
mkdir app
cd app
```

##### Optional: Have `python` invoke `python3`

```bash
sudo apt-get install python-is-python3`
```

##### Optional: For waveform generation support ([docs](https://github.com/bbc/audiowaveform#installation))

```bash
sudo add-apt-repository ppa:chris-needham/ppa
sudo apt-get update
sudo apt-get install audiowaveform
```

##### Optional: Further Ubuntu configuration

Under "Settings | Sharing", set the Computer Name to `vibin.local`.

```bash
sudo apt-get install net-tools
sudo apt-get install ssh
sudo apt-get install vim
```

##### Create python virtualenv

Add `venv` support to Ubuntu (assumes python 3.10; to confirm the required version, run
`python -m venv test` which will generate an error, where the error specifies the package to
install):

```bash
sudo apt-get install python3.10-venv
```

Create and activate the virtualenv:

```bash
python -m venv venv-vibin
source venv-vibin/bin/activate
```

## TODO

* Handle not discovering streamer and/or media
* Add cli command to only discover and display discovered devices  
* Do a black pass  
* Add version  
* Clean up Swagger configuration
* Investigate moving bulk of CXNv2 class (the shareable bits) to Streamer class  
* Do a docs pass
* Do a REST API pass  
* Do a logging pass
* Do a typing pass
* Write tests
* Rename files/structure (e.g. base.py is weird)
* Remove some flags from cli; e.g. --id (for play and browse)

## Notes

### Archive

```bash
tar -cvzf vibinserver,2021_03_39.tgz --exclude venv-vibin --exclude .idea vibin
```
