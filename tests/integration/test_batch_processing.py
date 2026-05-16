import requests
import time
import random
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from pathlib import Path

# Configuration
API_BASE = "http://localhost:8000/api/v1"
# Resolves to flipr/item_images/... regardless of working directory
IMAGE_PATH = Path(__file__).resolve().parent.parent.parent / "item_images" / "mens" / "tops" / "t_shirts"

def test_batch_processing():
    print("🧪 Testing Batch Processing API\n")

    # Step 1: Find all test images
    test_images = list(IMAGE_PATH.glob("*.jpg")) + list(IMAGE_PATH.glob("*.jpeg")) + list(IMAGE_PATH.glob("*.png"))

    if not test_images:
        print(f"❌ No test images found in {IMAGE_PATH}")
        print("Please update IMAGE_PATH in the script to point to your test images")
        return

    # Randomize selection
    random.shuffle(test_images)

    # Step 2: Encode multiple images to base64
    num_images = min(3, len(test_images))
    print(f"\n📸 Randomly selected {num_images} test images:")

    files = []

    final_images = test_images[:num_images]
    for image in final_images:
        files.append({
            "mime_type": "image/jpeg"
        })

    response = requests.post(
        f"{API_BASE}/batch/uploads",
        json={"files": files}
    )

    if response.status_code != 201:
        print(f"❌ Failed to upload images: {response.status_code}")
        print(response.text)
        return

    job_data = response.json()
    job_id = job_data["job_id"]
    print(f"✅ Job submitted: {job_id}")

    for upload, image in zip(job_data["uploads"], final_images):
        with open(image, "rb") as f:
            image_bytes = f.read()

        s3_response = requests.put(
            upload["presigned_url"],
            data=image_bytes,
            headers={"Content-Type": "image/jpeg"},
            verify=False
        )
        if s3_response.status_code != 200:
            print(f"❌ Failed to upload image to S3: {s3_response.status_code}")
            print(s3_response.text)
            return

        print(f"✅ Image uploaded to S3: {upload['s3_key']}")

    # Step 3: Submit batch job with multiple images
    print(f"\n📤 Submitting batch job with {num_images} images...")
    response = requests.post(
        f"{API_BASE}/batch/analyze",
        json={
            "job_id": job_id,
            "images": [{"s3_key": upload["s3_key"]} for upload in job_data["uploads"]],
            "metadata": {"test": "sprint_1_validation", "batch_size": num_images}
        }
    )

    if response.status_code != 201:
        print(f"❌ Failed to submit job: {response.status_code}")
        print(response.text)
        return

    job_data = response.json()
    job_id = job_data["job_id"]
    print(f"✅ Job submitted: {job_id}")


    # Step 4: Poll for completion
    print("\n⏳ Polling for completion...")
    max_polls = 30
    poll_interval = 2

    for i in range(max_polls):
        time.sleep(poll_interval)

        status_response = requests.get(f"{API_BASE}/batch/jobs/{job_id}")

        if status_response.status_code != 200:
            print(f"❌ Failed to get job status: {status_response.status_code}")
            return

        job_status = status_response.json()
        status = job_status["status"]
        progress = job_status["progress"]

        print(f"   Poll {i+1}: {status} ({progress['completed']}/{progress['total']} items, {progress['percentage']:.0f}%)")

        if status == "completed":
            print("\n✅ Job completed successfully!")

            # Display full results
            print("\n📋 Full Analysis Results:")
            for item in job_status["items"]:
                print(f"\n{'='*80}")
                print(f"📊 ITEM {item['index']}")
                print(f"{'='*80}")
                print(f"Status: {item['status']}")

                if item['status'] == 'success' and item.get('result'):
                    result = item['result']

                    # === METADATA ===
                    print("\n🔍 METADATA:")
                    metadata = result.get('metadata', {})
                    print(f"   Brand: {metadata.get('brand', 'None')}")
                    print(f"   Item Type: {metadata.get('item_type', 'Unknown')}")
                    print(f"   Color: {metadata.get('color', 'Unknown')}")
                    print(f"   Size: {metadata.get('size', 'Unknown')}")
                    print(f"   Condition: {metadata.get('condition', 'Unknown')}")
                    print(f"   Material: {metadata.get('material', 'Unknown')}")
                    print(f"   Search Query: {metadata.get('search_query', 'N/A')}")
                    if metadata.get('notable_details'):
                        print(f"   Notable Details: {', '.join(metadata['notable_details'])}")

                    # === PRICING COMPS ===
                    print("\n💰 PRICING COMPS:")
                    if result.get('comps'):
                        comps = result['comps']
                        print(f"   Search Query Used: {comps.get('search_query', 'N/A')}")
                        print(f"   Sample Size: {comps.get('sample_size', 0)} sold listings")
                        print(f"   Low Price: ${float(comps.get('low_price', 0)):.2f}")
                        print(f"   Median Price: ${float(comps.get('median_price', 0)):.2f}")
                        print(f"   High Price: ${float(comps.get('high_price', 0)):.2f}")
                        print(f"   ⭐ Suggested Price: ${float(comps.get('suggested_price', 0)):.2f}")

                        # Show a few example comps
                        raw_comps = comps.get('raw_comps', [])
                        if raw_comps:
                            print(f"\n   Example sold listings:")
                            for i, comp in enumerate(raw_comps[:3], 1):
                                print(f"      {i}. ${float(comp.get('sold_price', 0)):.2f} - {comp.get('title', 'N/A')}")

                    # === LISTINGS ===
                    print("\n📝 LISTING DRAFTS:")
                    listings = result.get('listings', [])
                    for listing in listings:
                        platform = listing.get('platform', 'unknown').upper()
                        print(f"\n   --- {platform} ---")
                        print(f"   Title: {listing.get('title', 'N/A')}")
                        print(f"   Price: ${float(listing.get('suggested_price', 0)):.2f}")
                        print(f"   Category: {listing.get('category_hint', 'N/A')}")

                        if listing.get('hashtags'):
                            hashtags = ' '.join(listing['hashtags'])
                            print(f"   Hashtags: {hashtags}")

                        description = listing.get('description', 'N/A')
                        # Truncate long descriptions for readability
                        if len(description) > 200:
                            description = description[:200] + "..."
                        print(f"   Description: {description}")
                        print(f"   " + "-"*40)

                elif item['status'] == 'failed':
                    print(f"\n❌ Error: {item.get('error', 'Unknown error')}")

            print(f"\n{'='*80}\n")
            return

        elif status == "failed":
            print(f"\n❌ Job failed!")
            print(job_status)
            return

    print(f"\n⏱️ Timeout: Job did not complete after {max_polls * poll_interval} seconds")
    print("Last status:", job_status)


if __name__ == "__main__":
    # Check if server is running
    try:
        health = requests.get("http://localhost:8000/health")
        if health.status_code == 200:
            print("✅ Server is running\n")
            test_batch_processing()
        else:
            print("❌ Server health check failed")
    except requests.exceptions.ConnectionError:
        print("❌ Server is not running!")
        print("Start it with: uvicorn main:app --reload")
