import argparse
import os
from PIL import Image
from transformers import DetrFeatureExtractor
from crop_images import load_model_from_ckpt
from os.path import dirname, abspath
import sys
project_dir = dirname(dirname(abspath(__file__)))
sys.path.append(project_dir)
from util.coco_relevent import CocoDetection
from util.visualize_and_process_bbox import visualize_predictions


def visualize(args, val_dataset, model, id2label):
    for i in range(args.visualize_number):
        pixel_values, target = val_dataset[i]
        pixel_values = pixel_values.unsqueeze(0)
        outputs = model(pixel_values=pixel_values, pixel_mask=None)

        image_id = target['image_id'].item()
        image = val_dataset.coco.loadImgs(image_id)[0]

        print(image['file_name'])
        image = Image.open(os.path.join(args.val_folder, image['file_name']))
        visualize_predictions(image, outputs, id2label)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str, required=True,
                        help="path to the directory that contains the split data")
    parser.add_argument('--checkpoint_path', type=str, required=True,
                        help="path to the checkpoint.")
    parser.add_argument('--visualize_number', type=int, default=5)
    args = parser.parse_args()


    model = load_model_from_ckpt(args)
    args.val_folder = os.path.join(args.data_dir, 'val')
    feature_extractor = DetrFeatureExtractor.from_pretrained("facebook/detr-resnet-50")
    val_dataset = CocoDetection(img_folder=args.val_folder, feature_extractor=feature_extractor,
                                train=False)
    cats = val_dataset.coco.cats
    id2label = {k: v['name'] for k, v in cats.items()}

    visualize(args, val_dataset, model, id2label)

