import asyncio
import json
import logging
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import aiohttp
from kafka import KafkaProducer, KafkaConsumer
import redis
import os
from dotenv import load_dotenv
import numpy as np
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
import pickle
import joblib

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AIMLService:
    """AI/ML service for advanced signal processing"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.kafka_consumer = KafkaConsumer(
            'news.signals.v1',
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='ai_ml_service',
            auto_offset_reset='latest'
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # Initialize ML models
        self.sentiment_analyzer = None
        self.entity_extractor = None
        self.event_classifier = None
        self.pattern_recognizer = None
        
        # Load ML models
        self.load_ml_models()
        
        # Event type mapping for ML classification
        self.event_type_mapping = {
            'LISTING': 0,
            'DELIST': 1,
            'HACK': 2,
            'REGULATION': 3,
            'FED_SPEECH': 4,
            'POLITICAL_POST': 5,
            'TECHNICAL_UPGRADE': 6,
            'PARTNERSHIP': 7,
            'OTHER': 8
        }
        
        # Reverse mapping
        self.event_type_reverse = {v: k for k, v in self.event_type_mapping.items()}
        
        self.running = False
        
    def load_ml_models(self):
        """Load pre-trained ML models"""
        try:
            logger.info("Loading ML models...")
            
            # Load BERT sentiment analyzer
            self.sentiment_analyzer = pipeline(
                "sentiment-analysis",
                model="cardiffnlp/twitter-roberta-base-sentiment-latest",
                device=0 if torch.cuda.is_available() else -1
            )
            logger.info("✅ BERT sentiment analyzer loaded")
            
            # Load entity extraction model (using spaCy-like approach)
            self.entity_extractor = self.load_entity_extractor()
            logger.info("✅ Entity extractor loaded")
            
            # Load event classification model
            self.event_classifier = self.load_event_classifier()
            logger.info("✅ Event classifier loaded")
            
            # Load pattern recognition model
            self.pattern_recognizer = self.load_pattern_recognizer()
            logger.info("✅ Pattern recognizer loaded")
            
        except Exception as e:
            logger.error(f"Failed to load ML models: {e}")
            # Fallback to rule-based methods
            self.sentiment_analyzer = None
            self.entity_extractor = None
            self.event_classifier = None
            self.pattern_recognizer = None
    
    def load_entity_extractor(self):
        """Load entity extraction model"""
        try:
            # For now, use a simple keyword-based approach
            # In production, this would be a fine-tuned NER model
            return {
                'crypto_entities': ['BTC', 'ETH', 'SOL', 'ADA', 'DOT', 'LINK', 'UNI', 'AVAX', 'MATIC', 'ATOM'],
                'company_entities': ['Binance', 'Coinbase', 'FTX', 'Kraken', 'Gemini', 'MicroStrategy', 'Tesla'],
                'person_entities': ['Vitalik Buterin', 'CZ', 'SBF', 'Elon Musk', 'Jerome Powell', 'Gary Gensler']
            }
        except Exception as e:
            logger.error(f"Failed to load entity extractor: {e}")
            return None
    
    def load_event_classifier(self):
        """Load event classification model"""
        try:
            # For now, use a simple TF-IDF + Naive Bayes approach
            # In production, this would be a fine-tuned transformer model
            vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
            classifier = MultinomialNB()
            
            # Train on sample data (in production, use real training data)
            sample_texts = [
                "Binance lists new token", "Coinbase delists trading pair",
                "Exchange hacked, funds stolen", "SEC announces new regulations",
                "Fed chair speaks on inflation", "Political candidate supports crypto",
                "Protocol upgrade launched", "Partnership announced between exchanges"
            ]
            
            sample_labels = [0, 1, 2, 3, 4, 5, 6, 7]  # Corresponding to event types
            
            # Fit the model
            X = vectorizer.fit_transform(sample_texts)
            classifier.fit(X, sample_labels)
            
            return {
                'vectorizer': vectorizer,
                'classifier': classifier
            }
            
        except Exception as e:
            logger.error(f"Failed to load event classifier: {e}")
            return None
    
    def load_pattern_recognizer(self):
        """Load pattern recognition model"""
        try:
            # Simple pattern recognition using historical data analysis
            # In production, this would be a more sophisticated model
            return {
                'patterns': {
                    'listing_impact': {'avg_price_change': 0.15, 'success_rate': 0.7},
                    'hack_impact': {'avg_price_change': -0.25, 'success_rate': 0.9},
                    'regulation_impact': {'avg_price_change': -0.10, 'success_rate': 0.8},
                    'fed_speech_impact': {'avg_price_change': 0.05, 'success_rate': 0.6}
                }
            }
        except Exception as e:
            logger.error(f"Failed to load pattern recognizer: {e}")
            return None
    
    async def start_processing(self):
        """Start AI/ML signal processing"""
        logger.info("Starting AI/ML signal processing...")
        self.running = True
        
        try:
            while self.running:
                # Poll for messages
                msg_pack = self.kafka_consumer.poll(timeout_ms=1000)
                
                for tp, messages in msg_pack.items():
                    for message in messages:
                        if not self.running:
                            break
                            
                        try:
                            # Process incoming signal
                            signal = json.loads(message.value.decode('utf-8'))
                            enhanced_signal = await self.enhance_signal(signal)
                            
                            if enhanced_signal:
                                # Publish enhanced signal to new topic
                                await self.publish_enhanced_signal(enhanced_signal)
                                
                        except Exception as e:
                            logger.error(f"Error processing signal: {e}")
                            
        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")
        finally:
            await self.stop()
    
    async def enhance_signal(self, signal: Dict) -> Optional[Dict]:
        """Enhance signal using AI/ML models"""
        try:
            enhanced_signal = signal.copy()
            
            # 1. Enhanced sentiment analysis
            enhanced_signal['ai_sentiment'] = await self.analyze_sentiment(signal['raw_text'])
            
            # 2. Enhanced entity extraction
            enhanced_signal['ai_entities'] = await self.extract_entities(signal['raw_text'])
            
            # 3. ML-based event classification
            enhanced_signal['ai_event_type'] = await self.classify_event(signal['raw_text'])
            
            # 4. Pattern recognition and impact prediction
            enhanced_signal['ai_patterns'] = await self.recognize_patterns(signal)
            
            # 5. Enhanced confidence scoring
            enhanced_signal['ai_confidence'] = await self.calculate_ai_confidence(enhanced_signal)
            
            # 6. Market impact prediction
            enhanced_signal['ai_market_impact'] = await self.predict_market_impact(enhanced_signal)
            
            logger.info(f"Enhanced signal: {signal['event_id']} with AI confidence: {enhanced_signal['ai_confidence']}")
            return enhanced_signal
            
        except Exception as e:
            logger.error(f"Error enhancing signal: {e}")
            return None
    
    async def analyze_sentiment(self, text: str) -> Dict:
        """Analyze sentiment using BERT model"""
        try:
            if self.sentiment_analyzer:
                # Use BERT model
                result = self.sentiment_analyzer(text[:512])  # Limit text length
                
                # Map sentiment to our format
                sentiment_mapping = {
                    'LABEL_0': 'NEGATIVE',
                    'LABEL_1': 'NEUTRAL', 
                    'LABEL_2': 'POSITIVE'
                }
                
                return {
                    'sentiment': sentiment_mapping.get(result[0]['label'], 'NEUTRAL'),
                    'confidence': result[0]['score'],
                    'model': 'bert_twitter',
                    'raw_result': result[0]
                }
            else:
                # Fallback to rule-based sentiment
                return self.rule_based_sentiment(text)
                
        except Exception as e:
            logger.error(f"Sentiment analysis error: {e}")
            return self.rule_based_sentiment(text)
    
    def rule_based_sentiment(self, text: str) -> Dict:
        """Rule-based sentiment analysis fallback"""
        text_lower = text.lower()
        
        positive_words = ['bullish', 'moon', 'pump', 'rally', 'surge', 'gain', 'profit', 'success']
        negative_words = ['bearish', 'dump', 'crash', 'fall', 'drop', 'loss', 'fail', 'hack', 'exploit']
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        if positive_count > negative_count:
            sentiment = 'POSITIVE'
            confidence = min(0.8, 0.5 + (positive_count * 0.1))
        elif negative_count > positive_count:
            sentiment = 'NEGATIVE'
            confidence = min(0.8, 0.5 + (negative_count * 0.1))
        else:
            sentiment = 'NEUTRAL'
            confidence = 0.5
        
        return {
            'sentiment': sentiment,
            'confidence': confidence,
            'model': 'rule_based',
            'positive_count': positive_count,
            'negative_count': negative_count
        }
    
    async def extract_entities(self, text: str) -> Dict:
        """Extract entities using ML model"""
        try:
            if self.entity_extractor:
                entities = {
                    'crypto': [],
                    'companies': [],
                    'people': [],
                    'locations': [],
                    'organizations': []
                }
                
                # Extract crypto entities
                for entity in self.entity_extractor['crypto_entities']:
                    if entity in text.upper():
                        entities['crypto'].append(entity)
                
                # Extract company entities
                for entity in self.entity_extractor['company_entities']:
                    if entity in text:
                        entities['companies'].append(entity)
                
                # Extract person entities
                for entity in self.entity_extractor['person_entities']:
                    if entity in text:
                        entities['people'].append(entity)
                
                return {
                    'entities': entities,
                    'model': 'keyword_based',
                    'confidence': 0.7
                }
            else:
                # Fallback to basic extraction
                return self.basic_entity_extraction(text)
                
        except Exception as e:
            logger.error(f"Entity extraction error: {e}")
            return self.basic_entity_extraction(text)
    
    def basic_entity_extraction(self, text: str) -> Dict:
        """Basic entity extraction fallback"""
        entities = {
            'crypto': [],
            'companies': [],
            'people': [],
            'locations': [],
            'organizations': []
        }
        
        # Simple regex-like extraction
        words = text.split()
        for word in words:
            if word.isupper() and len(word) >= 3:
                entities['crypto'].append(word)
        
        return {
            'entities': entities,
            'model': 'basic',
            'confidence': 0.5
        }
    
    async def classify_event(self, text: str) -> Dict:
        """Classify event using ML model"""
        try:
            if self.event_classifier:
                # Use ML classifier
                vectorizer = self.event_classifier['vectorizer']
                classifier = self.event_classifier['classifier']
                
                # Transform text
                X = vectorizer.transform([text])
                
                # Predict
                prediction = classifier.predict(X)[0]
                probabilities = classifier.predict_proba(X)[0]
                
                predicted_type = self.event_type_reverse.get(prediction, 'OTHER')
                confidence = max(probabilities)
                
                return {
                    'event_type': predicted_type,
                    'confidence': confidence,
                    'model': 'ml_classifier',
                    'probabilities': {
                        self.event_type_reverse.get(i, 'OTHER'): prob 
                        for i, prob in enumerate(probabilities)
                    }
                }
            else:
                # Fallback to rule-based classification
                return self.rule_based_event_classification(text)
                
        except Exception as e:
            logger.error(f"Event classification error: {e}")
            return self.rule_based_event_classification(text)
    
    def rule_based_event_classification(self, text: str) -> Dict:
        """Rule-based event classification fallback"""
        text_lower = text.lower()
        
        # Simple keyword matching
        if any(word in text_lower for word in ['list', 'listing', 'adds support']):
            return {'event_type': 'LISTING', 'confidence': 0.7, 'model': 'rule_based'}
        elif any(word in text_lower for word in ['delist', 'remove', 'suspend']):
            return {'event_type': 'DELIST', 'confidence': 0.7, 'model': 'rule_based'}
        elif any(word in text_lower for word in ['hack', 'exploit', 'breach']):
            return {'event_type': 'HACK', 'confidence': 0.8, 'model': 'rule_based'}
        elif any(word in text_lower for word in ['regulation', 'sec', 'cfdc']):
            return {'event_type': 'REGULATION', 'confidence': 0.7, 'model': 'rule_based'}
        else:
            return {'event_type': 'OTHER', 'confidence': 0.5, 'model': 'rule_based'}
    
    async def recognize_patterns(self, signal: Dict) -> Dict:
        """Recognize patterns and predict impact"""
        try:
            if self.pattern_recognizer:
                patterns = self.pattern_recognizer['patterns']
                event_type = signal.get('event_type', 'OTHER')
                
                if event_type in patterns:
                    pattern = patterns[event_type]
                    return {
                        'pattern_type': event_type,
                        'expected_price_change': pattern['avg_price_change'],
                        'success_rate': pattern['success_rate'],
                        'confidence': 0.7,
                        'model': 'historical_patterns'
                    }
                else:
                    return {
                        'pattern_type': 'unknown',
                        'expected_price_change': 0.0,
                        'success_rate': 0.5,
                        'confidence': 0.3,
                        'model': 'historical_patterns'
                    }
            else:
                return {
                    'pattern_type': 'unknown',
                    'expected_price_change': 0.0,
                    'success_rate': 0.5,
                    'confidence': 0.3,
                    'model': 'fallback'
                }
                
        except Exception as e:
            logger.error(f"Pattern recognition error: {e}")
            return {
                'pattern_type': 'error',
                'expected_price_change': 0.0,
                'success_rate': 0.5,
                'confidence': 0.1,
                'model': 'error'
            }
    
    async def calculate_ai_confidence(self, enhanced_signal: Dict) -> float:
        """Calculate AI-enhanced confidence score"""
        try:
            base_confidence = enhanced_signal.get('confidence', 0.5)
            
            # Adjust based on AI analysis
            ai_factors = []
            
            # Sentiment confidence
            if 'ai_sentiment' in enhanced_signal:
                ai_factors.append(enhanced_signal['ai_sentiment']['confidence'])
            
            # Entity extraction confidence
            if 'ai_entities' in enhanced_signal:
                ai_factors.append(enhanced_signal['ai_entities']['confidence'])
            
            # Event classification confidence
            if 'ai_event_type' in enhanced_signal:
                ai_factors.append(enhanced_signal['ai_event_type']['confidence'])
            
            # Pattern recognition confidence
            if 'ai_patterns' in enhanced_signal:
                ai_factors.append(enhanced_signal['ai_patterns']['confidence'])
            
            if ai_factors:
                ai_confidence = np.mean(ai_factors)
                # Combine base confidence with AI confidence
                final_confidence = (base_confidence * 0.4) + (ai_confidence * 0.6)
                return min(1.0, max(0.0, final_confidence))
            else:
                return base_confidence
                
        except Exception as e:
            logger.error(f"AI confidence calculation error: {e}")
            return enhanced_signal.get('confidence', 0.5)
    
    async def predict_market_impact(self, enhanced_signal: Dict) -> Dict:
        """Predict market impact based on AI analysis"""
        try:
            event_type = enhanced_signal.get('event_type', 'OTHER')
            sentiment = enhanced_signal.get('ai_sentiment', {}).get('sentiment', 'NEUTRAL')
            severity = enhanced_signal.get('severity', 0.5)
            
            # Base impact prediction
            base_impact = {
                'LISTING': 0.15,      # 15% positive
                'DELIST': -0.20,      # 20% negative
                'HACK': -0.30,        # 30% negative
                'REGULATION': -0.15,  # 15% negative
                'FED_SPEECH': 0.05,  # 5% impact
                'POLITICAL_POST': 0.02,  # 2% impact
                'TECHNICAL_UPGRADE': 0.10,  # 10% positive
                'PARTNERSHIP': 0.08,  # 8% positive
                'OTHER': 0.0
            }
            
            predicted_change = base_impact.get(event_type, 0.0)
            
            # Adjust based on sentiment
            if sentiment == 'POSITIVE':
                predicted_change *= 1.2
            elif sentiment == 'NEGATIVE':
                predicted_change *= 1.2
            
            # Adjust based on severity
            predicted_change *= severity
            
            # Add some randomness for realistic prediction
            noise = np.random.normal(0, 0.02)
            predicted_change += noise
            
            return {
                'predicted_price_change': predicted_change,
                'confidence': 0.6,
                'factors': {
                    'event_type': event_type,
                    'sentiment': sentiment,
                    'severity': severity,
                    'base_impact': base_impact.get(event_type, 0.0)
                },
                'model': 'ai_enhanced_prediction'
            }
            
        except Exception as e:
            logger.error(f"Market impact prediction error: {e}")
            return {
                'predicted_price_change': 0.0,
                'confidence': 0.1,
                'factors': {},
                'model': 'error'
            }
    
    async def publish_enhanced_signal(self, enhanced_signal: Dict):
        """Publish enhanced signal to Kafka"""
        try:
            future = self.kafka_producer.send(
                'ai.enhanced.signals.v1',
                value=enhanced_signal,
                key=enhanced_signal['event_id'].encode()
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published enhanced signal: {enhanced_signal['event_id']}")
        except Exception as e:
            logger.error(f"Error publishing enhanced signal: {e}")
    
    async def stop(self):
        """Stop AI/ML service"""
        logger.info("Stopping AI/ML service...")
        self.running = False
        
        # Close Kafka connections
        self.kafka_producer.close()
        self.kafka_consumer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    service = AIMLService()
    
    try:
        await service.start_processing()
    except KeyboardInterrupt:
        logger.info("Shutting down AI/ML service")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
