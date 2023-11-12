from dagshub.common.api.repo import RepoAPI
from dagshub.auth import get_token


def get_boto_client(repo_api: RepoAPI, token: str):
    endpoint_url = repo_api.repo_bucket_api_url()
    import boto3

    client = boto3.client("s3", endpoint_url=endpoint_url, aws_access_key_id=token, aws_secret_access_key=token)
    print(f"Client created. Use the name of the repo ({repo_api.repo_name}) as the name of the bucket")
    return client


def get_s3fs_client(repo_api: RepoAPI, token: str):
    endpoint_url = repo_api.repo_bucket_api_url()
    import s3fs

    client = s3fs.S3FileSystem(endpoint_url=endpoint_url, key=token, secret=token)
    print(f"Client created. Use the name of the repo ({repo_api.repo_name}) as the name of the bucket")
    return client


_s3_flavor_lookup = {
    "boto": get_boto_client,
    "s3fs": get_s3fs_client,
}


def get_repo_bucket_client(repo: str, flavor="boto"):
    """
    Creates an S3 client for the specified repository's DagsHub storage bucket

    Available flavors are:
        boto: Returns a `boto3.client` with predefined EndpointURL and credentials.
              The name of the bucket is the name of the repository,
              and you will need to specify it for any request you make
        s3fs: Returns a s3fs.S3FileSystem with predefined EndpointURL and credentials
              The name of the bucket is the name of the repository,
              and you will need to specify it for any request you make

    Args:
        repo: Name of the repo in the format of "username/repository"
        flavor: one of the possible s3 client flavor variants
    """
    repo_api = RepoAPI(repo)
    token = get_token()
    return _s3_flavor_lookup[flavor](repo_api, token)
